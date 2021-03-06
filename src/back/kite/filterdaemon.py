#!/usr/bin/env python
# filterdaemon.py 
# This background process creates indexes files for threads and filters.
#

import sys
import os
import pyinotify
import threading
import datetime
import time
import email.parser

import threads
import headers
import users
from cabinet import DatetimeCabinet
import maildir

# The architecture is pretty simple. This program is multithreaded.
# One thread is the producer. It uses inotify to detect changes to the
# fs. It writes in the events_queue.
#
# Another thread is the consumer. It processes the files to build an index of 
# threads (threads_index).
#
# Finally, there's a thread which periodically dumps the thread index.
events_queue = []

DUMPER_SLEEP_DURATION=20
EVENTS_QUEUE_PROCESSING_DELAY=10

class WatcherThread(threading.Thread):
    class EventHandler(pyinotify.ProcessEvent):
        def __init__(self, path):
            self.path = path

        def process_IN_CREATE(self, event):
            if event.pathname != self.path: # inotify also logs events at the root of the folder, which we don't care about
                events_queue.append({"type": "create", "path": event.pathname})

        def process_IN_DELETE(self, event):
            if event.pathname != self.path:
                events_queue.append({"type": "delete", "path": event.pathname})

    def __init__(self, path):
        threading.Thread.__init__(self)
        self.path = os.path.realpath(path)

    def run(self):
        wm = pyinotify.WatchManager()
        mask = pyinotify.IN_DELETE | pyinotify.IN_CREATE
        handler = WatcherThread.EventHandler(self.path)
        notifier = pyinotify.Notifier(wm, handler)
        wdd = wm.add_watch(self.path, mask, rec=True)
        notifier.loop()

class DumperThread(threading.Thread):
    def __init__(self, path, threads_index):
        threading.Thread.__init__(self)
        self.path = path
        self.threads_index = threads_index

    def run(self):
        while True:
            time.sleep(DUMPER_SLEEP_DURATION)
            self.threads_index.sync()
            
def process_new_email(path, threads_index):
    with open(path, "r") as fd:
        parser = email.parser.HeaderParser()
        email_headers = parser.parse(fd)
        
        subject = email_headers["subject"]
        from_field = {}
        from_field["name"], from_field["address"] = email.utils.parseaddr(email_headers["From"])
        to_field = {}
        to_field["addresses"] = email.utils.getaddresses(email_headers["to"])

        if subject != None:
            subject = headers.cleanup_subject(subject)
            thread = None
            for index, thr in enumerate(threads_index):
                if thr["subject"] == subject:
                    thread = threads_index.pop(index)
                    break

            if not thread:
                # create a new thread
                thread = threads.create_thread_structure()
                thread["subject"] = subject
                thread["creator"] = from_field

            msg_id = os.path.basename(path)
            thread["messages"].append(msg_id)
            thread["date"] = datetime.datetime.utcnow()

            if from_field["address"] != thread["creator"]["address"]:
                thread["lastreplyfrom"] = from_field

            threads_index.insert(0, thread)

class ProcessorThread(threading.Thread):
    def __init__(self, path, threads_index):
        threading.Thread.__init__(self)
        self.path = path
        self.threads_index= threads_index

    def run(self):
        while True:
            while len(events_queue) != 0:
                event = events_queue.pop(0)
                if event["type"] == "create":
                    try:
                        username = users.get_username_from_folder(event["path"]) 
                        print "username: %s, path: %s\n" % (username, event["path"])

                        if username not in self.threads_index:
                            print "Setting threads_index for user : %s" % username
                            self.threads_index[username] = {"threads_index": [], "dirty": True, "unread_count": 0}
                        
                        process_new_email(event["path"], self.threads_index[username]["threads_index"])
                        self.threads_index[username]["dirty"] = True
                        self.threads_index[username]["unread_count"] += 1
                    except IOError as e:
                        # This may be a Postfix/Dovecot temporary file. Ignore it.
                        print "caught ioerror %s" % e.strerror
                        pass
                    
            time.sleep(EVENTS_QUEUE_PROCESSING_DELAY)


if __name__ == "__main__":
    path = sys.argv[1]
    print "Watching %s..." % path

    threads_index = DatetimeCabinet("/home/kite/threads.db")

    watcher_thread = WatcherThread(path)
    processor_thread = ProcessorThread(path, threads_index)
    dumper_thread = DumperThread(path, threads_index)

    processor_thread.start()
    watcher_thread.start()
    dumper_thread.start()

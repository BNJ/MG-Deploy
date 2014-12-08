#!/usr/bin/env python
# -*- encoding: utf-8 -*-

# GUI Libraries
from Tkinter import *
from tkFont import Font
import tkMessageBox
from ttk import *

# Environment
import os, sys
import threading

# API Access
import urllib2
import paramiko, pipes
try:
	import json
except:
	import simplejson as json

# Configuration
from ConfigParser import SafeConfigParser

class App:
	"""Wrapper for all the application logic."""
	def get_configs(self):
		"""Reads all the deployment configs from deploy.conf into memory."""
		cfgfile = SafeConfigParser()
		cfgfile.read('deploy.conf')
		defaults = cfgfile.defaults()
		self.configs = {}
		for section in cfgfile.sections():
			secvals = defaults.copy()
			secvals.update(cfgfile.items(section))
			self.configs[section] = secvals
		return self.configs
		
	def make_button_box(self, frame, label, function):
		"""GUI helper. Makes a couple of horizontal buttons, a Cancel and some action."""
		buttonbox = Frame(frame)
		cancel = Button(buttonbox, text="Cancel", command=self.master.quit)
		cancel.pack(side=LEFT)

		run = Button(buttonbox, text=label, command=function, default=ACTIVE)
		run.pack(side=RIGHT,expand=1,fill=X)
		buttonbox.pack(side=RIGHT)

	def make_property_grid(self, frame, data):
		"""Very simply creates a table of keys/values."""
		pgrid = Frame(frame)
		bold = Font(weight='bold')
		reg  = Font()
		for row, (key, value) in enumerate(data.items()):
			label = Label(pgrid, text=key.strip().title() + ":",font=bold)
			label.grid(column=0, row=row, sticky=W)
			value = Label(pgrid, text=value.strip(), font=reg)
			value.grid(column=1, row=row, sticky=W)
		return pgrid
			
	def make_labeled_entry(self, frame, label, value="", password=False, readonly=False, multiline=False):
		"""Label and an entry box. Returns the entry box for reference."""

		tframe = Frame(frame)
		l = Label(tframe, text=label)
		l.pack(side=LEFT)

		args = {
		}
		if password:
			args['show'] = u'•'
		if readonly:
			args['state'] = DISABLED
		if multiline:
			EntryClass = Text
		else:
			EntryClass = Entry
		textEntry = EntryClass(tframe,**args)
		if (value):
			textEntry.insert(END,value)
		textEntry.pack(side=RIGHT, expand=1, fill=X)

		tframe.pack(side=TOP,expand=1,fill=X)
		return textEntry

	def ask_config(self):
		"""Displays a dialog asking which configuration you'd like to use."""
		if hasattr(self,'current_frame'):
			self.current_frame.pack_forget()
		configs = self.get_configs()
		ckeys = sorted(configs.keys())

		frame = Frame(self.master, borderwidth=10)
		l = Label(frame, text="Select a Blast Group")
		l.pack(side=TOP)

		scrollFrame = Frame(frame)
		scrollbar = Scrollbar(scrollFrame, orient=VERTICAL)
		lstConfig = Listbox(scrollFrame,yscrollcommand=scrollbar.set)
		scrollbar.config(command=lstConfig.yview)
		scrollbar.pack(side=RIGHT, fill=Y)
		for i, group in enumerate(ckeys):
			lstConfig.insert(END, group)

		lstConfig.pack(side=LEFT,fill=BOTH,expand=1)
		scrollFrame.pack(fill=BOTH,expand=1)
		self.lstConfig = lstConfig

		self.make_button_box(frame, "Select Deployment Configuration", self.config_selected)


		frame.pack(expand=1,fill=BOTH)
		self.current_frame = frame
		self.master.update()

	def config_selected(self):
		"""Handler once you've selected a configuration"""
		try:
			configidx = int(self.lstConfig.curselection()[0])
		except IndexError:
			tkMessageBox.showwarning("No config selected", "Please select a configuarion before continuing.")
			return
		confkey = self.lstConfig.get(configidx)
		self.config = self.configs[confkey]
		self.config['permutators'] = set(self.config['permutators'].split())

		self.current_frame.pack_forget()
		self.get_key()


	def get_key(self):
		frame = Frame(self.master, borderwidth=10)

		props = {}
		for version in self.config['versions'].split():
			props[version] = self.config['version_' + version]
		props['Permutators'] = ", ".join(self.config['permutators'])
		props['Servers'] = "\n".join(self.config['servers'].split())
		self.make_property_grid(frame, props).pack(expand=1,fill=BOTH)

		initial_message = "Deployed by %s"%(
			os.environ.get('USER','user')
		)
		self.gitmessage = self.make_labeled_entry(frame, "Git Commit Message", multiline=True, value=initial_message)

		self.passwordEntry = self.make_labeled_entry(frame, "SSH Key Password", password=True)
		self.passwordEntry.focus_set()
		self.passwordEntry.bind('<Return>', self.deploy)
		self.make_button_box(frame, "Deploy", self.deploy)
		frame.pack(expand=1,fill=BOTH)
		self.current_frame = frame
		self.master.update()

	def open_key(self):
		try:
			keyfile = self.config['identity'].format(**os.environ)
			password = self.passwordEntry.get()
			key = paramiko.RSAKey.from_private_key_file(
				keyfile,
				password
			)
		except paramiko.SSHException:
			tkMessageBox.showerror(
				"SSH Error",
				"I could not open the private key file. Did you enter the correct password?"
			)
			self.master.quit()
			sys.exit(-1)
		self.key = key
		return key
	def deploy_version(self, publish_url, progressbar, logvar):
		connections = []
		self.publishSemaphore = threading.Semaphore()
		self.gitLock = threading.RLock()
		key = self.key
		for server in self.config['servers'].split():
			conn = paramiko.SSHClient()
			conn.set_missing_host_key_policy(
				paramiko.AutoAddPolicy()
			)
			conn.connect(
				hostname=server,
				port=22,
				username=self.config['remoteuser'].format(**os.environ),
				pkey = key
			)
			sftp = conn.open_sftp()
			connections.append((conn,sftp))
		logvar.insert(END, "Waiting to publish" )
		self.publishSemaphore.acquire()
		if self.cancelled: 
			logvar.insert(END,  "Cancelled")
			logvar.yview(END)
			return
		logvar.insert(END, "Publishing" )
		pubresponse = urllib2.urlopen(publish_url)
		logvar.insert(END, "Published" )
		self.publishSemaphore.release()
		if self.cancelled: 
			logvar.insert(END,  "Cancelled")
			logvar.yview(END)
			return
		respo = json.loads(pubresponse.read())
		pubresponse.close()

		if self.cancelled: 
			logvar.insert(END,  "Cancelled")
			logvar.yview(END)
			return
		if respo['status'] != 'success':
			print "respo", respo
			logvar.insert(END,  "Error Publishing")
			for line in respo['data']['details'].splitlines():
				logvar.insert(END,  line)
			logvar.insert(END,  "Done")
			return
		data = respo['data']
		if self.cancelled: 
			logvar.insert(END,  "Cancelled")
			logvar.yview(END)
			return
		if data['status'] != 'success':
			print "data", data
			logvar.insert(END,  "Error Publishing")
			for line in data['data']['details'].splitlines():
				logvar.insert(END,  line)
			logvar.insert(END,  "Done")
			return

		permperm = self.config['permutators']
		todeploy = []
		for dname, fname in data['urls']:
			if self.cancelled: 
				logvar.insert(END,  "Cancelled")
				logvar.yview(END)
				return
			urlparts = fname.split('/')[4:-1]
			if len(urlparts) and urlparts[-1] == 'preview':
				urlparts = urlparts[:-1]
			permutator = '/' + '/'.join(urlparts)

			if (not ('*' in permperm or permutator in permperm )) or ('-'+permutator in permperm):
				continue
			todeploy.append(fname)

		progressbar['maximum'] = len(todeploy)
		progressbar['value'] = 0
		for fname in todeploy:
			if self.cancelled: 
				logvar.insert(END,  "Cancelled")
				logvar.yview(END)
				return

			target = '/'.join([
				self.config['publish_dir'].rstrip('/'),
				fname.lstrip('/')
			])
			fetchurl = "http://%s/al/%s"%(
				self.config['domain'].strip('/'),
				fname.lstrip('/')
			)
			dfile = urllib2.urlopen(fetchurl)
			files = []
			for ssh, sftp in connections:

				command = 'mkdir -p %s'%os.path.dirname(target)
				sin, sout, serr = ssh.exec_command(command)
				sout.read()
				err = serr.read()
				if err.strip():
					print err
					tkMessageBox.showwarning("Error making directory",err)

				try:
					sftp.stat(os.path.join(
						self.config['publish_dir'].rstrip('/'),
						'.git'
					))
				except IOError:
					command = 'git init %s' % self.config['publish_dir']
					with self.gitLock:
						sin, sout, serr = ssh.exec_command(command)
						sout.read()
						err = serr.read()
					if err.strip():
						print err
						tkMessageBox.showwarning('Git Error: Could not initialize directory', err)

				files.append(sftp.open(target, 'w'))
			buff = ""
			while True:
				buff = dfile.read(2048)
				if buff == "": break
				for f in files:
					f.write(buff)
			for f in files:
				f.close()
			dfile.close()

			command = 'cd %s && git add %s'%(
				self.config['publish_dir'],
				fname.lstrip('/')
			)
			for ssh, sftp in connections:
				with self.gitLock:
					sin, sout, serr = ssh.exec_command(command)
					sout.read()
					err = serr.read()
				if err.strip():
					print err
					tkMessageBox.showwarning("Git Error: Could not add %s"%fname, err)

			logvar.insert(END,  fname)
			logvar.yview(END)

			progressbar.step(1)

		command = 'cd %s && git commit -m %s' % ( 
			self.config['publish_dir'],
			pipes.quote(self.gitmessage.get("1.0",END))
		)
		for ssh, sftp in connections:
			with self.gitLock:
				sin, sout, serr = ssh.exec_command(command)
				sout.read()
				err = serr.read()
			if err.strip():
				print err
				tkMessageBox.showerror("Git Error: Could not commit", err)

		logvar.insert(END,  "Done")
		logvar.yview(END)
		progressbar.grid_forget()

		
		

	def deploy(self, event=None):
		self.current_frame.pack_forget()
		frame = Frame(self.master, borderwidth=10)

		progressgrid = Frame(frame)
		threads = []
		for row , version in enumerate(self.config['versions'].split()):
			version_url = self.config['version_'+version]
			pid, tid, vid = version_url.split('/')
			publish_url = "http://{domain}/al/versions/publish/{vid}/".format(
				domain = self.config['domain'],
				vid    = vid
			)
			label = Label(progressgrid, text=version.title())
			label.grid(row=row*2, column=0)

			pbar = Progressbar(progressgrid, maximum=100, value=0, length=600)
			pbar.grid(row=row*2, column=1)

			logframe = Frame(progressgrid)
			sb = Scrollbar(logframe)
			sb.pack(side=RIGHT, fill=Y)
			log = Listbox(logframe,yscrollcommand = sb.set, width=80)
			sb.config(command=log.yview)
			log.pack(side=LEFT,fill=BOTH, expand=1)
			logframe.grid(row=row*2+1, column=0, columnspan=2)

			threads.append(
				threading.Thread(
					target=self.deploy_version,
					args = [publish_url, pbar, log]
				)
			)


		self.threads = threads
		progressgrid.pack()

		buttonbox = Frame(frame)
		cancel = Button(buttonbox, command=self.cancel, text="Cancel")
		self.cancelbutton = cancel
		cancel.pack(side=LEFT)

		ok = Button(buttonbox, command=self.master.quit, state=DISABLED, text="OK", default=ACTIVE)
		ok.pack(side=RIGHT, expand=1, fill=X)

		buttonbox.pack(side=RIGHT)

		frame.pack()

		key = self.open_key()

		for t in threads:
			t.start()

		okthread = threading.Thread(target=self.make_it_ok, args=[threads, ok])
		okthread.start()


		self.master.update()

	def make_it_ok(self, threads, okbutton):
		for thread in threads:
			while thread.is_alive():
				thread.join(2)
		okbutton['state'] = NORMAL




	def get_groups(self):
		cur = self.cur
		cur.execute("SELECT DISTINCT group_id FROM `8`")
		return list(cur)
	@property
	def cancelled(self):
		self._cancel_lock.acquire()
		val = self._cancelled
		self._cancel_lock.release()
		return val
	@cancelled.setter
	def cancelled(self, val):
		self._cancel_lock.acquire()
		self._cancelled = val
		self._cancel_lock.release()

	def cancel(self):
		self.cancelled = True
		if hasattr(self,'cancelbutton'):
			getattr(self,'cancelbutton')['state'] = DISABLED
		
	def __init__(self, master):
		self.master = master

		master.wm_title("Deploy Magnolia Sites")

		self._cancel_lock = threading.RLock()
		self._cancelled = False

		self.ask_config()


# d = dialog.Dialog()
# d.setBackgroundTitle("Make URLs")
root = Tk()
s = Style()
s.theme_use('clam')

app = App(root)
root.mainloop()

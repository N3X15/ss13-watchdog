import cPickle
import json
import logging
import logging.handlers
import os
import psutil
import re
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib
import platform

script_dir = os.path.dirname(os.path.realpath(__file__))
sys.path.append(os.path.join(script_dir, 'lib', 'buildtools'))

from buildtools import *
from buildtools import os_utils
from buildtools.wrapper import Git
from buildtools.bt_logging import IndentLogger

class QChdir(Chdir):
	def __init__(self, newdir):
		Chdir.__init__(self, newdir, True)

# @formatting:off
default_config = {
	'monitor':{
		'ip':'127.0.0.1',
		'port':7777,
		'timeout': 30.0,
		'max-fails': 3,
		'wait-for-ready': True,
		'threads': False
	},
	'commands': {
		'compile': {
			'dme': 'baystation12.dme',
			'map-voting': {
				'match': 'maps[\\/]([a-z]+).dmm',
				'maps': {
					'Box Station': 'maps\tgstation.dmm',
					'Metaclub':    'maps\metaclub.dmm',
					'Deficiency':  'maps\defficiency.dmm',
				}
			}
		},
	},
	'paths': {
		'byond':      '~/byond',
		'stats':      'stats.json',
		'crashlog':   '~/byond/crashlogs/',
		'run':        '~/byond/tgstation/',
	},
	'git': {
		'game': {
			'remote': 'https://github.com/d3athrow/vgstation13.git',
			'branch': 'Bleeding-Edge',
			'path':   '~/byond/repos/game/',
		},
		'patches': {
			'remote': 'git@git.nexisonline.net:vgstation/secrets.git',
			'branch': 'master',
			'path':   '~/byond/repos/config/',
		},
		'config': {
			'remote': 'git@git.nexisonline.net:vgstation/config.git',
			'branch': 'master',
			'path':   '~/byond/repos/patches/',
		},
	},
	'nudge': {
		'id':'Test Server',
		'ip': 'localhost',
		'port': 45678,
		'key': 'my secret passcode'
	}
}
# @formatting:on

config = Config('config.yml', default_config)

last_response = {}

def send_nudge(message):
	if config.get('nudge',None) is None:
		return
	try:
		data = {}
		
		data['key'] = config.get('nudge.key', None)
		data['id'] = config.get('nudge.id', None)
		data['channel'] = 'nudges'
		data['data'] = message
		
		pickled = cPickle.dumps(data)
		s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		s.connect((config.get('nudge.ip', None), config.get('nudge.port', None)))
		s.send(pickled)
		s.close()
	except socket.error as e:
		print(str(e))
		return
	
def Compile(serverState, no_restart=False):
	global dd_proc
	global waiting_for_next_commit
	
	with QChdir(config.get('git.game.path')):
		currentCommit = Git.GetCommit()
		currentBranch = Git.GetBranch()
	
	log.info('Code is at {0} ({1}).  Triggering compile.'.format(currentCommit, currentBranch))
	
	for process in psutil.process_iter():
		try:
			if process.name() == DREAMDAEMON_IMAGE and process.is_running():
				log.info('Killing DreamDaemon PID#{}...'.format(process.pid))
				process.kill()
				process.wait()
		except psutil.AccessDenied:
			continue
	
	dme = config.get('commands.compile.dme', 'tgstation.dme')
	
	with os_utils.TimeExecution('Copy staging'):
		os_utils.copytree(os.path.abspath(config.get('git.game.path')), os.path.abspath(config.get('paths.run')), ignore=['.git/', '.bak'], verbose=False)
					
	if config.get('git.patches.path') is not None:
		with os_utils.TimeExecution('Copy patches'):
			os_utils.copytree(os.path.abspath(config.get('git.patches.path')), os.path.abspath(config.get('paths.run')), ignore=['.git/', '.bak'], verbose=False)
	
	map_find = re.compile('{}'.format(config.get('commands.compile.map-voting.match', None)))
	map_list = config.get('commands.compile.map-voting.maps', {'':None})
	with QChdir(config.get('paths.run')):
		base_dmb = None
		for map_name, map_filename in map_list.items():
			map_outdir = None
			if map_find is not None and map_filename is not None:
				map_outdir = os.path.join(config.get('paths.run'), 'maps', 'voting', map_name)
				with log.info('Patching {}...'.format(dme)):
					fn, ext = os.path.splitext(dme)
					new_dme = fn + '.mdme'
					ln = 0
					changes = 0
					with open(dme, 'r') as orig:
						with open(new_dme, 'w') as new:
							for line in orig:
								ln += 1
								origline = line = line.strip()
								line, nchange = map_find.subn(map_filename, line)
								if nchange > 0:
									log.info('Changed line #{}.'.format(ln))
									log.info(' - '+origline)
									log.info(' + '+line)
									changes += 1
								new.write(line + '\n')
				if os.path.isfile(dme):
					os.remove(dme)
				os.rename(new_dme, dme)
				
				log.info('Wrote {}, {} changes.'.format(dme, changes))
		
			# Compile
			warnings = 0
			errors = 0
			msg = 'Compiling...'
			if map_find is not None and map_filename is not None:
				msg = 'Compiling for {}...'.format(map_name)
				if not os.path.isdir(map_outdir):
					os.makedirs(map_outdir)
			with log.info('Compiling...'):
				stdout, stderr = cmd_output([DREAMMAKER_EXE, dme], echo=True)
				locked = False
				if stdout or stderr:
					skip_next_errors = 0
					
					for line in (stdout + stderr).split('\n'):
						
						line = line.strip()
						if line.startswith('BUG:') and line.endswith('is locked up!'):
							# BUG: The file /home/gmod/byond/tgstation/baystation12.mdme.rsc is locked up!
							skip_next_errors += 1
							log.error(line)
							if not locked:
								send_nudge('COMPILE ERROR: RSC file is locked! Skipping further RSC errors.')
								locked = True
						elif 'error:' in line or 'BUG:' in line:
							errors += 1
							if skip_next_errors > 0:
								skip_next_errors -= 1
								continue 
							log.error(line)
							nudge='COMPILE ERROR: {0}'.format(line)
							if errors <= 10:
								continue
							if errors == 10:
								nudge += ' (10 errors occurred, hiding further errors.)'
							send_nudge(nudge)
						elif 'warning:' in line:
							warnings += 1
							log.warn(line)
						elif line.strip() != '':
							log.info(line)
					
			if errors > 0:
				msg = 'Compile failed ({} warnings, {} errors). Waiting for next commit.'.format(warnings, errors)
				send_nudge(msg)
				log.warn(msg)
				waiting_for_next_commit = True
				return
			elif map_outdir is not None:
				output_file = os.path.join(map_outdir, config.get('compile.dme', 'baystation12.dmb'))
				log.info('Copying {} to {}...'.format(getDMB(), output_file))
				shutil.move(getDMB(), output_file)
				if base_dmb is None:
					base_dmb = output_file
				send_nudge('Completed updating {}...'.format(map_name))
	
		if base_dmb is not None:
			shutil.copy2(base_dmb, config.get('compile.dme', 'baystation12.dmb'))
			log.info('Copied {} to {}...'.format(base_dmb, config.get('compile.dme', 'baystation12.dmb')))
			
	next_nudge = 'Update completed ({} warnings). Restarting...'.format(warnings)
	if waiting_for_next_commit:
		next_nudge = 'Update completed ({} warnings), and successfully compiled! Restarting...'.format(warnings)
		waiting_for_next_commit = False
		
	updateConfig()
		
	lastCommits['game'] = currentCommit

	if serverState:
		send_nudge(next_nudge)
		log.info(next_nudge)
	
	# Recheck in a bit to be sure
	lastState = False
	
	if not no_restart:
		restartServer()
	
def PerformServerReadyCheck(serverState):
	global waiting_on_server_response
	global last_response
	if not waiting_on_server_response:
		return
	
	# with QChdir(config.get('git.game.path')):
	# 	currentCommit = Git.GetCommit()
	# 	currentBranch = Git.GetBranch()
	
	updatereadyfile = os.path.join(config.get('paths.run'), 'data', 'UPDATE_READY.txt')
	serverreadyfile = os.path.join(config.get('paths.run'), 'data', 'SERVER_READY.txt')
	srf_exists = os.path.isfile(serverreadyfile)
	if not srf_exists and 'players' in last_response:
		srf_exists = last_response['players'] == 0
	nudgemsg = "Server has "
	if (srf_exists):
		nudgemsg += "sent the READY signal."
	elif (not serverState):
		nudgemsg += "exited."
	if srf_exists or not serverState:
		send_nudge(nudgemsg + ' Now recompiling.')
		waiting_on_server_response = False
		if srf_exists:
			os.remove(serverreadyfile)
		if os.path.isfile(updatereadyfile):
			os.remove(updatereadyfile)
		Compile(serverState)
		
def checkForUpdates(serverState):
	global lastCommits
	global waiting_on_server_response
	global waiting_for_next_commit
	global last_response
	
	updated = False
	for reponame, cfg in config.cfg['git'].items():
		if checkForUpdate(serverState, reponame, cfg):
			updated = True
			
	if updated:
		if config.get('monitor.wait-for-ready', True) and not waiting_for_next_commit:
			# if not waiting_on_server_response:
			waiting_on_server_response = True
			log.info('Waiting for server to exit.')
			send_nudge('Waiting for server to exit.')

			commit = ''
			with QChdir(config.get('git.game.path')):
				commit = Git.GetCommit()
				
			with open(os.path.join(config.get('paths.run'), 'data', 'UPDATE_READY.txt'), 'w') as updatenotice:
				updatenotice.write('{GIT_REMOTE}/{GIT_BRANCH} {GIT_COMMIT}'.format(GIT_REMOTE=config.get('git.game.remotename', 'origin'), GIT_COMMIT=commit, GIT_BRANCH=config.get('git.game.branch', 'master')))
				
			PerformServerReadyCheck(serverState)
			return
		else:
			Compile(serverState)
	else:
		PerformServerReadyCheck(serverState)
		
def updateConfig(): 
	if config.get('git.config') is not None:
		with os_utils.TimeExecution('Copy config'):
			cfgPath = os.path.abspath(config.get('git.config.path'))
			gamePath = os.path.abspath(config.get('paths.run'))
			gameConfigPath = os.path.join(gamePath,'config')
			
			# cmd(['cp', '-a', cfgPath, gamePath])
			os_utils.copytree(cfgPath, gameConfigPath, ignore=['.git/', '.bak', 'mode.txt'], verbose=False)
		
			# Copy gamemode, if it exists.
			botConfigSource = os.path.join(cfgPath, 'mode.txt')
			botConfigDest = os.path.join(gamePath, 'data', 'mode.txt')
			
			if os.path.isfile(botConfigSource):
				if os.path.isfile(botConfigDest):
					os.remove(botConfigDest)
				shutil.move(botConfigSource, botConfigDest)
				
			# Update MOTD
			inputRules = os.path.join(cfgPath, 'motd.txt')
			outputRules = os.path.join(gamePath, 'config', 'motd.txt')
			with open(inputRules, 'r') as template:
				with open(outputRules, 'w') as motd:
					for _line in template:
						line = _line.format(GIT_BRANCH=config.get('git.game.branch', 'master'), GIT_REMOTE=config.get('git.game.remotename', 'origin'), GIT_COMMIT=config.get('git.game.commit', '???'))
						motd.write(line)
		
def checkForUpdate(serverState, reponame, cfg):
	global lastCommits

	remote_uri = cfg['remote']
	remote_name = cfg.get('remotename', 'origin')
	branch = cfg.get('branch', 'master')
	dest = cfg['path']
	
	
	changed = False
	if not os.path.isdir(dest):
		send_nudge('({reponame}) Performing initial clone of {GIT_REMOTE}!'.format(reponame=reponame, GIT_REMOTE=remote_name))
		cmd(['git', 'clone', remote_uri, dest], critical=True, echo=True)
		changed = True
		
	with QChdir(dest):
		# subprocess.call('git pull -q -s recursive -X theirs {0} {1}'.format(GIT_REMOTE,GIT_BRANCH),shell=True)
		cmd(['git', 'fetch', '-q', remote_name])
		# cmd(['git', 'clean', '-fdx', '{0}/{1}'.format(remote_name, branch)])
		# cmd(['git', 'reset', '--hard', '{0}/{1}'.format(remote_name, branch)]) 
		cmd(['git', 'checkout', '-q', '{0}/{1}'.format(remote_name, branch)]) 
		currentCommit = Git.GetCommit()
		currentBranch = Git.GetBranch()
		config['git'][reponame]['commit'] = currentCommit
		if reponame in lastCommits and currentCommit != lastCommits[reponame]:
			if not changed:
				msg = '({reponame}) Updating server to {GIT_REMOTE}/{GIT_COMMIT}!'.format(reponame=reponame, GIT_REMOTE=remote_name, GIT_COMMIT=currentCommit)
				log.info(msg)
				send_nudge(msg)
			cmd(['git', 'reset', '--hard', '{0}/{1}'.format(remote_name, branch)])
			changed = True
			lastCommits[reponame] = currentCommit
	return changed

# Return True for success, False otherwise.
def open_socket():
	# Open TCP socket to target.
	s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
	s.connect((config.get('monitor.ip'), config.get('monitor.port')))
	# 30-second timeout
	s.settimeout(config.get('monitor.timeout', 30.0))
	return s
	
# Snippet below from http://pastebin.com/TGhPBPGp
def decode_packet(packet):
	if packet != "":
		if packet[0] == b'\x00' or packet[1] == b'\x83':  # make sure it's the right packet format
			# Actually begin reading the output:
			sizebytes = struct.unpack('>H', packet[2] + packet[3])  # array size of the type identifier and content # ROB: Big-endian!
			# print(repr(sizebytes))
			size = sizebytes[0] - 1  # size of the string/floating-point (minus the size of the identifier byte)
			if packet[4] == b'\x2a':  # 4-byte big-endian floating-point
				unpackint = struct.unpack('f', packet[5] + packet[6] + packet[7] + packet[8])  # 4 possible bytes: add them up together, unpack them as a floating-point
				return unpackint[1]
			elif packet[4] == b'\x06':  # ASCII string
				unpackstr = ''  # result string
				index = 5  # string index
							   
				while (size > 0):  # loop through the entire ASCII string
					size -= 1
					unpackstr = unpackstr + packet[index]  # add the string position to return string
					index += 1
				return unpackstr.replace('\x00', '')
	log.error('UNKNOWN PACKET: {0}'.format(repr(packet)))
	return b''
       
def findDD():
	global dd_proc, DREAMDAEMON_IMAGE
	if dd_proc is None or dd_proc.is_running():
		dd_proc = None
		for proc in psutil.process_iter():
			try:
				if proc.name() == DREAMDAEMON_IMAGE:
					dd_proc = proc
					log.info('Found DreamDaemon running as process #{}'.format(dd_proc.pid))
					break
			except psutil.AccessDenied:
				continue
			
def getDMB(ignore_mapvoting=False):
	dme_filename = config.get('compile.dme', 'baystation12.dmb')
	
	#if config.get('commands.compile.map-voting.match', None) is not None and not ignore_mapvoting:
	#	filename, ext = os.path.splitext(dme_filename)
	#	dme_filename = filename + '.mdme.dmb'
	return dme_filename
			
def restartServer():
	global dd_proc
	findDD()
	if dd_proc is not None and dd_proc.is_running():
		dd_proc.kill()
		dd_proc = None
		log.warn('DreamDaemon still running, process killed.')
		send_nudge('DreamDaemon still running, process killed.')
	
	dme_filename = getDMB(ignore_mapvoting=True)
	
	# DreamDaemon baystation12 1336 -trusted -threads off
	args = [
		dme_filename,
		config.get('monitor.port', 7777),
		'-trusted'
	]
	
	if not config.get('monitor.threads', False) and platform.system() != 'Windows':
		args += ['-threads', 'off']
		
	with QChdir(config.get('paths.run')):
		cmd_daemonize([DREAMDAEMON_EXE] + args, critical=True)
	
def ping_server(request):
	global last_response
	try:
		# Snippet below from http://pastebin.com/TGhPBPGp
		#==============================================================
		# All queries must begin with a question mark (ie "?players")
		if request[0] != b'?':
			request = b'?' + request
		   
		# --- Prepare a packet to send to the server (based on a reverse-engineered packet structure) --- 
		query = b'\x00\x83' 
		query += struct.pack('>H', len(request) + 6)  # Rob: BIG-endian
		query += b'\x00\x00\x00\x00\x00'
		query += request
		query += b'\x00'
		#==============================================================

		s = open_socket()
		if s is None:
			return False
		
		# print 'Sending query packet...'
		s.sendall(query)
		# print 'Receiving response...'
		data = b''
		while True:
			buf = s.recv(1024)
			data += buf
			szbuf = len(buf)
			# print('<',szbuf)
			if szbuf < 1024:
				break
		s.close()
		
		response = decode_packet(data)
		
		if response is not None:
			response = response.replace('\x00', '')
			# print 'Received: ', response
		
			parsed_response = {}
			reserved_keys = ['ai', 'respawn', 'admins', 'players', 'host', 'version', 'mode', 'enter', 'vote', 'playerlist']
			for chunk in response.split('&'):
				dt = chunk.split('=')
				if dt[0] not in reserved_keys:
					if 'playerlist' not in parsed_response:
						parsed_response['playerlist'] = []
					parsed_response['playerlist'] += [ dt[0] ]
				else:
					parsed_response[dt[0]] = ''
					if len(dt) == 2:
						parsed_response[dt[0]] = urllib.unquote(dt[1])
			last_response = parsed_response
			# print 'Received: ', repr(parsed_response) #, response
			# {'ai': '1', 'respawn': '0', 'admins': '0', 'players': '0', 'host': '', 'version': '/vg/+Station+13', 'mode': 'secret', 'enter': '1', 'vote': '0'}
			with open(config.get('paths.stats'), 'w') as f:
				json.dump(parsed_response, f)
		else:
			log.error("Received NONE from server!")
			return False
	except socket.timeout:
		log.error("Socket timed out!")
		return False
	except socket.error:
		log.error("Connection lost!")
		return False
	return True

LOGPATH = config.get('paths.crashlog', 'logs')

if not os.path.isdir(LOGPATH):
	os.makedirs(LOGPATH)
	
logFormatter = logging.Formatter(fmt='%(asctime)s [%(levelname)-8s]: %(message)s', datefmt='%m/%d/%Y %I:%M:%S %p')  # , level=logging.INFO, filename='crashlog.log', filemode='a+')
log = logging.getLogger()
log.setLevel(logging.INFO)

fileHandler = logging.handlers.RotatingFileHandler(os.path.join(LOGPATH, 'crash.log'), maxBytes=1024 * 1024 * 50, backupCount=0)  # 50MB
fileHandler.setFormatter(logFormatter)
log.addHandler(fileHandler)

log = IndentLogger(log)
# consoleHandler = logging.StreamHandler()
# consoleHandler.setFormatter(logFormatter)
# log.addHandler(consoleHandler)

log.info('-' * 10)
log.info('/vg/station Watchdog: Started.')
send_nudge('Watchdog script restarted.')
lastState = True
failChain = 0
firstRun = True
lastCommits = {}
lastResponse = {}
dd_proc = None
waiting_on_server_response = False
waiting_for_next_commit = False

MAX_FAILURES = config.get('monitor.max-fails')

# Set up env first
byond_base = os.path.abspath(config.get('paths.byond', '~/byond'))
byond_bin = os.path.abspath(os.path.join(byond_base, 'bin'))
byond_man = os.path.abspath(os.path.join(byond_base, 'man'))

is_posix = platform.system() != 'Windows'

# Should probably make this configurable.
DREAMDAEMON_IMAGE = 'DreamDaemon' if is_posix else 'dreamdaemon.exe'
DREAMDAEMON_EXE = 'DreamDaemon' if is_posix else os.path.join(byond_bin,'dreamdaemon.exe')

DREAMMAKER_EXE = 'DreamMaker' if is_posix else os.path.join(byond_bin,'dm.exe')

# Does the job of byondsetup.
if is_posix:
	ENV.merge({
		'BYOND_SYSTEM': byond_base,
		
		'PATH':            ':'.join([byond_bin] + os.environ['PATH'].split(':')),
		'LD_LIBRARY_PATH': ':'.join([byond_bin] + os.environ.get('LD_LIBRARY_PATH', '').split(':')),
		'MANPATH':         ':'.join([byond_man] + os.environ.get('MANPATH', '').split(':'))
	})

findDD()

# Gather initial repo states.
with log.info('Gathering git repository statuses...'):
	for reponame, repocfg in config['git'].items():
		repopath = repocfg['path']
		if os.path.isdir(repopath):
			with QChdir(repopath):
				lastCommits[reponame] = Git.GetCommit()
				currentBranch = Git.GetBranch()
			log.info('{0} repository on branch {1}, commit {2}.'.format(reponame.capitalize(), currentBranch, lastCommits[reponame]))
		else:
			log.warn('{0} repository ({1}) is missing!'.format(reponame.capitalize(), repopath))
		
checkForUpdates(True)
	
dmb_filename = getDMB(ignore_mapvoting=True)
dmb_filepath = os.path.join(config.get('paths.run'), dmb_filename)

need_compile=False
if not os.path.isfile(dmb_filepath):
	msg = 'Main DMB ({}) missing!'.format(os.path.basename(dmb_filename))
	log.warn(msg)
	send_nudge(msg)
	need_compile = True
	
map_list = config.get('commands.compile.map-voting.maps', {'':None})
for map_name, map_filename in map_list.items():
	if map_filename is not None:
		map_dmb = os.path.join(config.get('paths.run'), 'maps', 'voting', map_name, os.path.basename(dmb_filename))
		if not os.path.isfile(map_dmb):
			need_compile = True
			msg = 'DMB for map {} missing!'.format(map_dmb)
			log.warn(msg)
			send_nudge(msg)
		
if need_compile: 
	Compile(False, no_restart=True)

updateConfig()

waiting_on_server_response = os.path.isfile(os.path.join(config.get('paths.run'), 'data', 'UPDATE_READY.txt'))
if waiting_on_server_response:
	log.warn('Server waiting to die.')
	
while True:
	if waiting_for_next_commit:
		checkForUpdates(False)
		if waiting_for_next_commit:
			time.sleep(50)
			continue
	if not ping_server(b'?status'):
		# try to start the server again
		checkForUpdates(False)
		failChain += 1
		if lastState == False:
			if failChain > MAX_FAILURES:
				send_nudge('Watchdog script has failed to restart the server.')
				log.error('Too many failures, quitting!')
				sys.exit(1)
			log.error('Try {0}/{1}...'.format(failChain, MAX_FAILURES))
			send_nudge('Try {0}/{1}...'.format(failChain, MAX_FAILURES))
		else:
			log.error("Detected a problem, attempting restart ({0}/{1}).".format(failChain, MAX_FAILURES))
			send_nudge('Attempting restart ({0}/{1})...'.format(failChain, MAX_FAILURES))
		restartServer()
		time.sleep(50)  # Sleep 50 seconds for a total of almost 2 minutes before we ping again.
		lastState = False
	else:
		if lastState == False:
			log.info('Server is confirmed to be back up and running.')
			send_nudge('Server is back online and responding to queries.')
		if firstRun:
			log.info('Server is confirmed to be up and running.')
			send_nudge('Server is online and responding to queries.')
		else:
			checkForUpdates(True)
		
		lastState = True
		failChain = 0
	firstRun = False
	time.sleep(50)  # 50 seconds between "pings".

import cPickle
import json
import logging
import logging.handlers
import os
import platform
import re
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib

import psutil
from buildtools import *
from buildtools import os_utils
from buildtools.bt_logging import IndentLogger
from buildtools.wrapper import Git

script_dir = os.path.dirname(os.path.realpath(__file__))
sys.path.append(os.path.join(script_dir, 'lib', 'buildtools'))

chosen_map = ""
compiling = False
missing_maps = []


class QChdir(Chdir):

    def __init__(self, newdir):
        Chdir.__init__(self, newdir, True)

# @formatting:off
default_config = {
    'monitor': {
        'ip': '127.0.0.1',
        'port': 7777,
        'timeout': 90.0,
        'max-fails': 3,
        'wait-for-ready': True,
        'threads': False
    },
    'commands': {
        'compile': {
            'dme': 'vgstation13.dme',
            'map-voting': {
                'match': 'maps[\\/]([a-z]+).dm',
                'maps': {
                    'Box Station': 'maps\tgstation.dm',
                    'Metaclub':    'maps\metaclub.dm',
                    #'Defficiency':  'maps\defficiency.dm',
                    'Taxi Station':  'maps\taxistation.dm',
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
            'remote': 'git@origin.com:vgstation/vgstation.git',
            'branch': 'Bleeding-Edge',
            'path':   './repos/game',
        },
        'patches': {
            'remote': 'git@git.nexisonline.net:vgstation/secrets.git',
            'branch': 'master',
            'path':   '~/byond/repos/patches/',
        },
        #'config': {
        #		'remote': 'git@git.nexisonline.net:vgstation/config.git',
        #		'branch': 'master',
        #		'path':   '~/byond/repos/config/',
        #	},
    },
    'nudge': {
        'id': 'Test Server',
        'ip': 'localhost',
        'port': 45678,
        'key': 'my secret passcode'
    }
}
# @formatting:on

config = Config('config.yml', default_config)

last_response = {}

compile_interrupted = False


def send_nudge(message):
    if config.get('nudge', None) is None:
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
    global chosen_map
    global compiling
    global compile_interrupted

    if compiling:
        return False
    compiling = True
    compile_interrupted = False
    with QChdir(config.get('git.game.path')):
        currentCommit = Git.GetCommit()
        currentBranch = Git.GetBranch()

    log.info('Code is at {0} ({1}).  Triggering compile.'.format(currentCommit, currentBranch))

    # for process in psutil.process_iter():
    #	try:
    #		if process.name() == DREAMDAEMON_IMAGE and process.is_running():
    #			log.info('Killing DreamDaemon PID#{}...'.format(process.pid))
    #			process.kill()
    #			process.wait()
    #	except psutil.AccessDenied:
    #		continue

    dme = config.get('commands.compile.dme', 'tgstation.dme')
    # with os_utils.TimeExecution('Making Changelogs'):
    #subprocess.call("c:\\users\\ss13.WIN-P0RHEL3A9QS\\desktop\\makechangelog.bat", shell=True)

    if config.get('git.patches.path') is not None:
        with os_utils.TimeExecution('Copy patches'):
            os_utils.copytree(os.path.abspath(config.get('git.patches.path')), os.path.abspath(config.get('git.game.path')), ignore=['.git/', '.bak'], verbose=True, ignore_mtime=True)

    map_find = re.compile('{}'.format(config.get('commands.compile.map-voting.match', None)))
    map_list = config.get('commands.compile.map-voting.maps', {'': None})
    with QChdir(config.get('git.game.path')):
        #zippity = os.path.join(config.get('git.game.path'), 'zippitydooda.py')
        #subprocess.call('python {}'.format(zippity), shell=True)
        base_dmb = None
        for map_name, map_filename in map_list.items():
            map_outdir = None
            # if(len(missing_maps)):
            # if(missing_maps.count(map_name) == 0):
            #log.info("Skipping map {} for compiling.".format(map_name))
            #send_nudge("Skipping map {} for compiling.".format(map_name))
            # continue

            if map_find is not None and map_filename is not None:
                map_outdir = os.path.join(config.get('git.game.path'), 'maps', 'voting', map_name)
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
                                    log.info(' - ' + origline)
                                    log.info(' + ' + line)
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
                            nudge = 'COMPILE ERROR: {0}'.format(line)
                            if errors > 10:
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
                compiling = False
                compile_interrupted = False
                return False
            elif map_outdir is not None:
                projectname, ext = os.path.splitext(os.path.basename(dme))
                dmbfilename = projectname + '.dmb'
                output_file = os.path.join(os.path.abspath(map_outdir), dmbfilename)
                log.info('Copying {} to {}...'.format(getDMB(), output_file))
                shutil.move(getDMB(), output_file)
                if base_dmb is None:
                    base_dmb = output_file
                send_nudge('Completed updating {}...'.format(map_name))
            for reponame, cfg in config.cfg['git'].items():
                if checkForUpdate(serverState, reponame, cfg):
                    compile_interrupted = True
                    compiling = False
                    send_nudge('Received new commit during compile sequence, aborting and restarting...')
                    log.info('Interrupted by new commit...')
                    return False
    next_nudge = 'Update completed ({} warnings). Waiting for server to die...'.format(warnings)
    missing_maps[:] = []
    if waiting_for_next_commit:
        next_nudge = 'Update completed ({} warnings), and successfully compiled! Waiting for server to die...'.format(warnings)
        waiting_for_next_commit = False
    with QChdir(config.get('git.game.path')):
        map_config = config.get('commands.compile.map-voting.maps', {'', None})
        if map_config is not None:
            if chosen_map == "" or chosen_map is None:
                chosen_map = 'Box Station'
            if chosen_map != "" and chosen_map is not None:
                dme = config.get('commands.compile.dme', 'tgstation.dme')
                projectname, ext = os.path.splitext(os.path.basename(dme))
                dmbfilename = projectname + '.dmb'
                base_dmb = os.path.join(os.path.abspath(os.path.join(config.get('git.game.path'), 'maps', 'voting', chosen_map)), dmbfilename)
                shutil.copy2(base_dmb, dmbfilename)

    # with os_utils.TimeExecution('Copy config'):
        # updateConfig()

    lastCommits['game'] = currentCommit

    if serverState:
        send_nudge(next_nudge)
        log.info(next_nudge)

    # Recheck in a bit to be sure
    lastState = False
    compiling = False
    compile_interrupted = False
    # if not no_restart:
    # restartServer()
    return True


def PerformServerReadyCheck(serverState):
    global waiting_on_server_response
    global last_response
    global chosen_map
    if not waiting_on_server_response:
        return

    # with QChdir(config.get('git.game.path')):
    # 	currentCommit = Git.GetCommit()
    # 	currentBranch = Git.GetBranch()

    updatereadyfile = os.path.join(config.get('paths.run'), 'data', 'UPDATE_READY.txt')
    serverreadyfile = os.path.join(config.get('paths.run'), 'data', 'SERVER_READY.txt')
    srf_exists = os.path.isfile(serverreadyfile)
    if srf_exists:
        log.warning("server ready file exists...")
    if not srf_exists and 'players' in last_response:
        srf_exists = last_response['players'] == 0
        log.info("ready file does not exist, player count is " + last_response['players'])
    nudgemsg = "Server has "
    if (srf_exists):
        nudgemsg += "sent the READY signal."
    elif (not serverState):
        nudgemsg += "exited."
    if srf_exists or not serverState:
        send_nudge(nudgemsg + ' Now recompiling.')
        waiting_on_server_response = False
        if srf_exists:
            file = open(serverreadyfile, 'r')
            chosen_map = file.readline().strip()
            log.warning("chosen map is " + chosen_map)
            file.close()
            os.remove(serverreadyfile)
        if os.path.isfile(updatereadyfile):
            os.remove(updatereadyfile)
        CopyBinaries(serverState)


def CopyBinaries(serverState):
    global compiling
    global chosen_map
    global lastCommits
    global waiting_on_server_response
    global waiting_for_next_commit
    global last_response

    if compiling:
        send_nudge('Waiting for compile to finish...')
        log.info('Waiting for compile to finish...')
        while compiling:
            time.sleep(50)
    next_nudge = ""
    send_nudge('Copying updated files over.')
    log.info('Copying updated files over.')
    for process in psutil.process_iter():
        try:
            if process.name() == DREAMDAEMON_IMAGE and process.is_running():
                log.info('Killing DreamDaemon PID#{}...'.format(process.pid))
                process.kill()
                process.wait()
        except:
            continue

    subprocess.call("taskkill /F /IM DreamDaemon.exe")
    rsc_path = os.path.join(os.path.abspath(config.get('paths.run')), 'vgstation13.rsc')
    iteration = 0
    while os.path.isfile(rsc_path):
        if iteration > 3:
            send_nudge('Unable to remove dirty RSC, panic')
            log.warning('Unable to remove dirty RSC, panic')
            sys.exit()
        iteration += 1
        os.remove(rsc_path)
        if not os.path.isfile(rsc_path):
            send_nudge('Removed Dirty RSC')
            log.info('Removed Dirty RSC')
        else:
            send_nudge('Could not remove dirty RSC, trying again in a few seconds...')
            log.warning('Could not remove dirty RSC, trying again in a few seconds...')
            sleep(5)
    folder = os.path.join(os.path.abspath(config.get('paths.run')), 'rsc')
    try:
        for the_file in os.listdir(folder):
            file_path = os.path.join(folder, the_file)
            try:
                if os.path.isfile(file_path) and file_path.endswith('.zip'):
                    os.unlink(file_path)
            except:
                continue
    except:
        log.warning('exception occurred in copying')
    with os_utils.TimeExecution('Copy staging from {} to {}'.format(os.path.abspath(config.get('git.game.path')), os.path.abspath(config.get('paths.run')))):
        os_utils.copytree(os.path.abspath(config.get('git.game.path')), os.path.abspath(config.get('paths.run')), ignore=['.git/', '.bak'], verbose=True)
    with QChdir(config.get('paths.run')):
        map_config = config.get('commands.compile.map-voting.maps', {'', None})
        if map_config is not None:
            if chosen_map == "" or chosen_map is None:
                chosen_map = 'Box Station'
            if chosen_map != "" and chosen_map is not None:
                dme = config.get('commands.compile.dme', 'tgstation.dme')
                projectname, ext = os.path.splitext(os.path.basename(dme))
                dmbfilename = projectname + '.dmb'
                base_dmb = os.path.join(os.path.abspath(os.path.join(config.get('paths.run'), 'maps', 'voting', chosen_map)), dmbfilename)
                shutil.copy2(base_dmb, dmbfilename)
                log.info('Copied {} to {}...'.format(base_dmb, dmbfilename))
        if chosen_map != "":
            next_nudge += 'New map is {}'.format(chosen_map)
        if next_nudge != "" and next_nudge is not None:
            send_nudge(next_nudge)
            log.info(next_nudge)
    restartServer()


def checkForUpdates(serverState, forced=False):
    global lastCommits
    global waiting_on_server_response
    global waiting_for_next_commit
    global last_response
    global compiling
    global need_binary
    global compile_interrupted

    updated = False
    for reponame, cfg in config.cfg['git'].items():
        if checkForUpdate(serverState, reponame, cfg):
            updated = True

    if updated or forced or compile_interrupted:
        success = Compile(serverState)
        if need_binary or compile_interrupted or not success:
            return
        if config.get('monitor.wait-for-ready', True) and not compiling and not waiting_for_next_commit:
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
        elif not waiting_for_next_commit:
            log.info('not compiling and not going to wait for server or commit')
            CopyBinaries(serverState)
            return
        elif forced and not serverState:
            PerformServerReadyCheck(serverState)
    else:
        if need_binary:
            return
        PerformServerReadyCheck(serverState)


def updateConfig():
    cfgPath = os.path.abspath(config.get('git.config.path'))
    gamePath = os.path.abspath(config.get('paths.run'))
    gameConfigPath = os.path.join(gamePath, 'config')

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
    #inputRules = os.path.join(cfgPath, 'motd.txt')
    #outputRules = os.path.join(gamePath, 'config', 'motd.txt')
    # with open(inputRules, 'r') as template:
    #	with open(outputRules, 'w') as motd:
    #		for _line in template:
    #			line = _line.format(GIT_BRANCH=config.get('git.game.branch', 'master'), GIT_REMOTE=config.get('git.game.remotename', 'origin'), GIT_COMMIT=config.get('git.game.commit', '???'))
    #			motd.write(line)


def checkForUpdate(serverState, reponame, cfg):
    global lastCommits

    remote_uri = cfg['remote']
    remote_name = cfg.get('remotename', 'origin')
    branch = cfg.get('branch', 'Bleeding-Edge')
    dest = cfg['path']

    changed = False
    if not os.path.isdir(dest):
        send_nudge('({reponame}) Performing initial clone of {GIT_REMOTE}!'.format(reponame=reponame, GIT_REMOTE=remote_name))
        cmd(['git', 'clone', remote_uri, dest], critical=True, echo=True)
        changed = True

    with QChdir(dest):
        log.info(dest)
        # subprocess.call('git pull -q -s recursive -X theirs {0} {1}'.format(GIT_REMOTE,GIT_BRANCH),shell=True)
        with log.info('Fetching changes for ' + remote_name):
            #os.system("git fetch -q " + remote_name)
            cmd_output(['git', 'fetch', '-q', '{0}'.format(remote_name)], echo=True)
            #subprocess.call('git fetch -q {0}'.format(remote_name), shell=True)

        #cmd(['git', 'fetch', '-q', remote_name])
        # cmd(['git', 'clean', '-fdx', '{0}/{1}'.format(remote_name, branch)])
        #cmd(['git', 'reset', '--hard', '{0}/{1}'.format(remote_name, branch)])

        checkout_tries = 1
        need_checkout = True
        while(need_checkout and checkout_tries < 3):
            #subprocess.call('git checkout -q {0}/{1}'.format(remote_name,branch), shell=True)
            #cmd(['git', 'checkout', '-q', '{0}/{1}'.format(remote_name, branch)])
            with log.info('Checking out ' + branch):
                stdout, stderr = cmd_output(['git', 'checkout', '-q', '{0}/{1}'.format(remote_name, branch)], echo=True)
                locked = False
                if stdout or stderr:

                    for line in (stdout + stderr).split('\n'):

                        line = line.strip()
                        if line.startswith('error:') and line.endswith('overwritten by checkout:'):
                            cmd_output(['git', 'clean', '-f', '-d'], echo=True)
                            cmd_output(['git', 'reset', '--hard', '{0}/{1}'.format(remote_name, branch)], echo=True)
                            break
                need_checkout = False
            log.info("{0} and {1} tries".format(need_checkout, checkout_tries))
        if(need_checkout):
            send_nudge('Attempt at checking out ' + remote_name + '/' + branch + 'failed with too many tries.')
            return
        currentCommit = Git.GetCommit()
        currentBranch = Git.GetBranch()
        config['git'][reponame]['commit'] = currentCommit
        if reponame in lastCommits and currentCommit != lastCommits[reponame]:
            if not changed:
                msg = '({reponame}) Updating server to {GIT_REMOTE}/{GIT_COMMIT}!'.format(reponame=reponame, GIT_REMOTE=remote_name, GIT_COMMIT=currentCommit)
                log.info(msg)
                send_nudge(msg)
            stdout, stderr = cmd_output(['git', 'clean', '-f', '-d'], echo=True)
            #cmd(['git', 'reset', '--hard', '{0}/{1}'.format(remote_name, branch)])
            if stdout or stderr:
                skip_next_errors = 0

                for line in (stdout + stderr).split('\n'):

                    line = line.strip()
                stdout, stderr = cmd_output(['git', 'reset', '--hard', '{0}/{1}'.format(remote_name, branch)], echo=True)
            #cmd(['git', 'reset', '--hard', '{0}/{1}'.format(remote_name, branch)])
            if stdout or stderr:
                skip_next_errors = 0

                for line in (stdout + stderr).split('\n'):

                    line = line.strip()
                    log.info(line)
            stdout, stderr = cmd_output(['git', 'clean', '-f', '-d'], echo=True)
            #cmd(['git', 'reset', '--hard', '{0}/{1}'.format(remote_name, branch)])
            if stdout or stderr:
                skip_next_errors = 0

                for line in (stdout + stderr).split('\n'):

                    line = line.strip()
            #cmd(['git', 'clean', '-f', '-d'])
            changed = True
            lastCommits[reponame] = currentCommit
            cmd_output(['git push -u TEMPORAARY Bleeding-Edge'])
            # git push -u TEMPORAARY Bleeding-Edge
    return changed

# Return True for success, False otherwise.


def open_socket():
    # Open TCP socket to target.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((config.get('monitor.ip'), config.get('monitor.port')))
    # 30-second timeout
    s.settimeout(config.get('monitor.timeout', 90.0))
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
    dme_filename = config.get('compile.dme', 'vgstation13.dmb')

    # if config.get('commands.compile.map-voting.match', None) is not None and not ignore_mapvoting:
    #	filename, ext = os.path.splitext(dme_filename)
    #	dme_filename = filename + '.mdme.dmb'
    return dme_filename


def restartServer():
    global dd_proc
    findDD()
    if dd_proc is not None and dd_proc.is_running():
        try:
            dd_proc.kill()
        except:
            log.warn('Dream Daemon process unable to be closed')
        dd_proc = None
        log.warn('DreamDaemon still running, process killed.')
        send_nudge('DreamDaemon still running, process killed.')

    #dme_filename = getDMB(ignore_mapvoting=True)

    # DreamDaemon vgstation13 1336 -trusted -threads off
    # args = [
    #	dme_filename,
    #	config.get('monitor.port', 7777),
    #	'-trusted'
    #]

    # if not config.get('monitor.threads', False) and platform.system() != 'Windows':
    #	args += ['-threads', 'off']

    # with QChdir(config.get('paths.run')):
        #cmd_daemonize(['dreamdaemon c:\\users\\ss13.WIN-P0RHEL3A9QS\\desktop\\vgstation13-testing\\vgstation13.dmb 7777 -trusted'], echo=True, critical=True)
    subprocess.call("C:\\Users\\ss13.WIN-P0RHEL3A9QS\\Desktop\\start-testing.bat", shell=True)


def ping_server(request, fug=0):
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
                    parsed_response['playerlist'] += [dt[0]]
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
        if not(fug >= 2):
            log.error("Attempting to reconnect, try #%d" % (fug + 1))
            time.sleep(60)
            return ping_server(request, fug + 1)
        return False
    except socket.error:
        log.error("Connection lost! try #%d" % (fug))
        if not(fug >= 2):
            log.error("Attempting to reconnect, try #%d" % (fug + 1))
            time.sleep(120)
            return ping_server(request, fug + 1)
        return False
    if(fug > 0):
        log.info("Connection with the server reestablished")
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

DREAMDAEMON_IMAGE = 'DreamDaemon' if is_posix else 'dreamdaemon.exe'
DREAMDAEMON_EXE = 'DreamDaemon' if is_posix else os.path.join(byond_bin, 'dreamdaemon.exe')

DREAMMAKER_EXE = 'DreamMaker' if is_posix else os.path.join(byond_bin, 'dm.exe')

# Does the job of byondsetup.
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

need_compile = False
need_staging_compile = False
need_binary = False
checkForUpdates(True)

staging_filename = os.path.join(config.get('git.game.path'), 'vgstation13.dmb')
dmb_filename = config.get('commands.compile.dmb')
dmb_filepath = os.path.join(config.get('paths.run'), dmb_filename)


if not os.path.isfile(dmb_filepath):
    msg = 'Main DMB ({}) missing!'.format(os.path.basename(dmb_filename))
    log.warn(msg)
    send_nudge(msg)
    need_binary = True

if not os.path.isfile(staging_filename):
    log.warn('Staging DMB is missing')
    send_nudge('Staging DMB is missing')
    need_staging_compile = True
map_list = config.get('commands.compile.map-voting.maps', {'': None})
for map_name, map_filename in map_list.items():
    if map_filename is not None:
        map_dmb = os.path.join(config.get('paths.run'), 'maps', 'voting', map_name, os.path.basename(dmb_filename))
        if not os.path.isfile(map_dmb):
            need_staging_compile = True
            msg = 'DMB for map {} missing!'.format(map_name)
            log.warn(msg)
            send_nudge(msg)
            missing_maps.append(map_name)

if need_compile or compile_interrupted or need_staging_compile and not compiling:
    checkForUpdates(ping_server(b'?status', 3), True)
    #Compile(False, no_restart=True)
if need_binary and not compiling:
    checkForUpdates(ping_server(b'?status', 3))
    CopyBinaries(False)
    need_binary = False

# with os_utils.TimeExecution('Copy config'):
    # updateConfig()

waiting_on_server_response = os.path.isfile(os.path.join(config.get('paths.run'), 'data', 'UPDATE_READY.txt'))
if waiting_on_server_response:
    log.warn('Server waiting to die.')
else:
    if need_compile and not compiling and not waiting_for_next_commit:
        CopyBinaries(False)

while True:
    if waiting_for_next_commit:
        checkForUpdates(ping_server(b'?status', 3))
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

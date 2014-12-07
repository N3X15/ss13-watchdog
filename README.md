# /vg/station Server Watchdog 2.0

A configurable SS13 server monitoring and auto-update tool.

## License

See LICENSE.

## Installation

### Windows

0. Install BYOND.
1. Install Python 2.7 from http://python.org and ensure it's in PATH
2. Download and run [get-pip.py](https://raw.githubusercontent.com/pypa/pip/master/contrib/get-pip.py).
3. Open cmd.exe and run ```pip install psutil PyYaml```
4. Install a Git CLI (command line client) if it's not already present.
5. In cmd.exe, run: ```git clone https://github.com/N3X15/ss13-watchdog ss13 && cd ss13 && git submodule update --init --recursive```
6. Edit the config.yml.dist file to taste, and save it as config.yml.
7. Launch Watchdog.py

### Linux

0. Create a user for the gameserver
1. Install BYOND
2. Run the following:

```
## AS WHEEL USER:
# Install pip and git.
sudo apt-get install python-pip git

# Install PyYaml and psutils
sudo pip install psutil PyYaml

## AS GAMESERVER USER:
# 
git clone https://github.com/N3X15/ss13-watchdog ss13 && cd ss13 && git submodule update --init --recursive
cp config.yml.dist config.yml
editor config.yml
# Edit config.yml to taste

# Start installing /vg/station (or whatever you selected):
python Watchdog.py

# OR, if you want to run it in a screen:
screen -dmS SS13W python Watchdog.py
```

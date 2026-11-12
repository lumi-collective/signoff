@echo off
rem Runs Signoff from this plugin folder; the arguments are passed on as given.
py -3 -I "%~dp0..\signoff\signoff.py" %*

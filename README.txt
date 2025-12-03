Use Instructions:
1. run the "ipconfig show" (windows cmd) or other methods to determine the server's IP
2. Edit HOST variable in Source/server.py and Source/client.py to match server's host IP (line 12 of server and line 9 of host)
3. Ensure python is installed on path using command "py --version"
4. Run command "pip install pygame" (May be requires to downgrade python if using more recent versions of python)
5. Open two command promps, navigate to "\CEG4188_Project_Group1\Source"
6. On first command prompt run command "py server.py"
7. On second command prompt run command "py client.py"
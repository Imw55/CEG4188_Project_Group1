import socket
import json
import threading
import sys
import pygame
import time

#ENTER SERVER IP HERE
HOST = "192.168.2.36"
#--------------------
PORT = 8000

#Display Constants
SCREEN_WIDTH = 1000
SCREEN_HEIGHT = 600
FPS = 60

#Gameplay Constants
AUTO_PICKUP_RADIUS = 40.0
AUTO_PICKUP_COOLDOWN = 0.6

#RGB Colour Codes
WHITE = (255,255,255)
BLACK = (0,0,0)
RED   = (255,0,0)
BLUE  = (0,128,255)
GREEN = (0,255,0)
YELLOW = (255,255,0)

def send_json(sock, obj):
    '''
    Sends JSON-Based message over desired socket
    sock: socket.socket object over which the json will be sent
    obj: JSON message to be sent
    '''

    try:
        msg = json.dumps(obj) + "\n"
        sock.sendall(msg.encode("utf-8"))
    except Exception as e:
        print("[client] send_json error:", e)

class Client:
    '''
    Class representing the client-side game data. Responsible for keeping game-state, recieving
    and processing server messages, and transmitting server requests
    self.sock: socket.socket object containing clients connection to server
    self.pid: Player identifier for the game, initialized by server
    self.team: Team for which the client belongs to, "red" or "blue"
    self.players: A list of players (including client's player) involved in the session.
    includes pid, player position, and flag status
    self.flags: A list of flag objects in session. Includes their team ("red" or "blue"), position,
    and the pid of the carrying player
    self.score: A tuple containing the score for each team "red" and "blue
    self.over: A boolean representing the state of the game
    self.winner: The team that has won the game "red" or "blue
    self.listener_thread: Thread used for listening for server messages
    self.lock: Mutex lock for listener_thread
    '''
    def __init__(self, host=HOST, port=PORT):

        #Establish connection to server and allow blocking
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        print("[client] Connecting...")
        self.sock.connect((host, port))
        print("[client] Connected.")

        self.sock.setblocking(True)

        #Initialize class variables
        self.pid = None
        self.team = None
        self.players = {}
        self.flags = []
        self.score = {"red":0, "blue":0}
        self.over = False
        self.winner = None

        #Initialize locking and start thread
        self.lock = threading.Lock()
        self._last_auto_pickup = {}

        self.listener_thread = threading.Thread(target=self.listen, daemon=True)
        self.listener_thread.start()

    def listen(self):
        '''
        Listens on client socket for messages from server, processes JSON, and calls
        handler
        '''

        #Initialize buffer and run untill end of session
        buffer = ""
        while not self.over:
            try:

                #Recieve data from server
                data = self.sock.recv(4096)
                if not data:
                    self.over = True
                    break

                #Buffer data
                buffer += data.decode("utf-8")

                #Split messages by newline delimitor
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if not line.strip():
                        continue
                    try:
                        #Create and handle JSON message
                        msg = json.loads(line)
                        self.handle(msg)
                    except json.JSONDecodeError:
                        print("[client] Invalid JSON:", line)

            except Exception as e:
                print("[client] Listener error:", e)
                self.over = True
                break

    def handle(self, msg):
        '''
        Handles message from server based on messaging scheme
        msg: JSON message recieved from server
        '''

        #Get message type field
        mtype = msg.get("type")

        #If connection message, retrieve pid and team from server
        if mtype == "connect":
            self.pid = msg.get("pid")
            self.team = msg.get("team")

        #If disconnection message, end session
        elif mtype == "disconnect":
            self.over = True

        #If update message, update current game-state variables to server's version
        elif mtype == "update":
            new_players = {}
            #Add newly connected players to game-state
            for p in msg.get("players", []):
                pid = p.get("pid")
                if pid is not None:
                    new_players[pid] = p
            #Call lock and update flags and scores game-state
            with self.lock:
                self.players = new_players
                self.flags = msg.get("flags", [])
                self.score = msg.get("scores", self.score)

        #If over message, end session and determine the winner
        elif mtype == "over":
            self.over = True
            self.winner = msg.get("winner")

    def send_input(self, up, down, left, right):
        '''
        Sends input message type as a JSON to server as per messaging scheme
        up: Boolean representing whether player is moving up
        down: Boolean representing whether player is moving down
        left: Boolean representing whether player is moving left
        right: Boolean representing whether player is moving right
        '''
        
        #Send JSON message over client socket as per message scheme
        send_json(self.sock, {
            "type": "input",
            "move": {
                "up": bool(up),
                "down": bool(down),
                "left": bool(left),
                "right": bool(right)
            }
        })

    def send_pickup(self):
        '''
        Sends pickup message type as JSON as per messaging scheme
        '''

        #Send JSON message over client socket as per messaging scheme
        send_json(self.sock, {
            "type": "pickup"
        })
    
    def send_disconnect(self):
        '''
        Sends disconnect message type as JSON as per messaging scheme, terminates session,
        and closes socket
        '''
        try:
            #Send JSON message over client socket as per messaging scheme
            send_json(self.sock, {
                "type": "disconnect",
                "pid": self.pid
            })
        except Exception:
            pass
        #Terminate session
        self.over = True
        try:
            #Close socket
            self.sock.close()
        except:
            pass

    def try_auto_pickup(self):
        '''
        Attempts to pickup shared flag object for player character based on current game data, if successful
        sends a pickup message to the server for confirmation. Pickup is confirmed or denied by next update message
        '''

        #If no one exists do nothing
        if self.pid is None or self.team is None: return

        with self.lock:
            
            #Find player character
            our_player = self.players.get(self.pid)
            if our_player is None: return

            #Get player coordinates
            px = our_player.get("x")
            py = our_player.get("y")
            if px is None or py is None: return

            #For pickup request cooldown
            now = time.time()

            #For each flag check pickup conditions
            for f in self.flags:
                
                #Check that flag is not our own and that no player is carrying
                if f.get("team") == self.team: continue
                if f.get("carrier") is not None: continue

                #Get flag coordinates
                fx = f.get("x")
                fy = f.get("y")
                if fx is None or fy is None: continue

                #Check if flag is eligible for pickup by distance
                dx = px - fx
                dy = py - fy
                dist_sq = dx*dx + dy*dy
                if dist_sq <= AUTO_PICKUP_RADIUS * AUTO_PICKUP_RADIUS:
                    
                    key = f.get("team", f.get("colour", "unknown"))
                    last = self._last_auto_pickup.get(key, 0.0)

                    #If cooldown is expired send pickup request
                    if now - last >= AUTO_PICKUP_COOLDOWN:
                        self._last_auto_pickup[key] = now
                        self.send_pickup()
                        return
                    
    def try_steal(self):
        '''
        Attempts to steal shared flag object for player character based on current game data, if successful
        sends a steal message to the server for confirmation. Pickup is confirmed or denied by next update message
        '''

        #If no one exists do nothing
        if self.pid is None or self.team is None: return

        with self.lock:
            
            #Get player character
            our_player = self.players.get(self.pid)
            if our_player is None: return

            #Get player coordinates
            px = our_player.get("x")
            py = our_player.get("y")
            if px is None or py is None: return

            #For all players check if a steal can be performed
            for p in self.players.values():
                
                #Check player is not on player team
                if p["pid"] == self.pid: continue
                if p["team"] == self.team: continue
                if not (p.get("red") or p.get("blue")): continue

                #Get target player coordinates
                ox = p.get("x")
                oy = p.get("y")
                if ox is None or oy is None: continue

                dx = px - ox
                dy = py - oy
                dist_sq = dx*dx + dy*dy

                #Check if player is within steal radius
                if dist_sq <= AUTO_PICKUP_RADIUS**2:

                    #If all conditions are met send steal request to server
                    flag_team = "red" if p.get("red") else "blue"
                    send_json(self.sock, {
                        "type": "steal",
                        "target": p["pid"],
                        "flag": flag_team
                    })
                    return

def render(screen, client):
    '''
    Renders the visuals for the CTF client including all players, scores, and flags.
    Renders only one frame so must be used within a loop
    screen: pygame.display object for displaying the game
    client: client object containing the game-state
    '''

    #Initialize screen and draw scores
    screen.fill((30,30,30))
    font = pygame.font.SysFont(None, 36)
    red_score = client.score.get("red", 0)
    blue_score = client.score.get("blue", 0)
    screen.blit(font.render(f"Red: {red_score}", True, WHITE), (30, 30))
    screen.blit(font.render(f"Blue: {blue_score}", True, WHITE), (30, 66))

    #Get players and flags from client state
    with client.lock:
        players = list(client.players.values())
        flags = list(client.flags)

    #For every player draw their model;
    for p in players:
        #Check team for colour
        team = p.get("team")
        if team == 'red': colour = RED
        else: colour = BLUE

        #Get player coordinates
        x = p.get("x")
        y = p.get("y")
        if x is None or y is None: continue

        #Draw player, if player has a flag draw indicator
        pygame.draw.circle(screen, colour, (int(x), int(y)), 12)
        if p.get("red") or p.get("blue"):
            pygame.draw.circle(screen, YELLOW, (int(x), int(y)), 18, 2)

    #For every flag draw their model
    for f in flags:

        #Get flag colour
        fcolour = f.get("team")
        if fcolour == 'red': colour = RED
        else: colour = BLUE

        #Draw flag if it is unclaimed
        if f.get("carrier") is None:

            #Get flag coordinates
            fx = f.get("x")
            fy = f.get("y")
            if fx is None or fy is None: continue

            #Draw flag model with team colour
            pygame.draw.rect(screen, colour, (int(fx)-10, int(fy)-10, 20, 20))

def main():
    
    #Initialization
    pygame.init()
    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
    pygame.display.set_caption("CTF Client")

    clock = pygame.time.Clock()

    client = Client(HOST, PORT)

    #Current key-press state for movement
    keys_pressed = {
        "UP": False,
        "DOWN": False,
        "LEFT": False,
        "RIGHT": False
    }

    running = True

    #Main loop
    while running:

        #Slow loop to 60FPS
        clock.tick(FPS)

        #Exit if session terminated
        if client.over:
            time.sleep(3)
            break
        
        #Get current keypress state
        for event in pygame.event.get():

            #Exit mechanism
            if event.type == pygame.QUIT:
                running = False

            #Check for up-keys
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_w:
                    keys_pressed["UP"] = True
                if event.key == pygame.K_s:
                    keys_pressed["DOWN"] = True
                if event.key == pygame.K_a:
                    keys_pressed["LEFT"] = True
                if event.key == pygame.K_d:
                    keys_pressed["RIGHT"] = True

            #Check for down-keys
            elif event.type == pygame.KEYUP:
                if event.key == pygame.K_w:
                    keys_pressed["UP"] = False
                if event.key == pygame.K_s:
                    keys_pressed["DOWN"] = False
                if event.key == pygame.K_a:
                    keys_pressed["LEFT"] = False
                if event.key == pygame.K_d:
                    keys_pressed["RIGHT"] = False

        #Transmit input-type message to server
        client.send_input(keys_pressed["UP"], keys_pressed["DOWN"], keys_pressed["LEFT"], keys_pressed["RIGHT"])

        #Run flag checks
        client.try_auto_pickup()
        client.try_steal()

        #Display game-state
        render(screen, client)
        pygame.display.flip()

    #Run disconnect sequence
    client.send_disconnect()
    pygame.quit()
    sys.exit()

if __name__ == "__main__":
    main()

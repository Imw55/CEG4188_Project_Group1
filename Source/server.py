import socket
import threading
from threading import Lock
import json
import time
import random
from queue import Queue, Empty
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

#ENTER SERVER IP HERE
HOST = "192.168.2.36"
#--------------------
PORT = 8000

#Server Constants
TICK_RATE = 20.0
BUFFER_SIZE = 4096
SCORE_TO_WIN = 5
PICKUP_RADIUS = 30.0
STEAL_CHANCE = 0.35
BASE_RADIUS = 50.0
MAP_WIDTH = 1000
MAP_HEIGHT = 600

@dataclass
class Player:
    '''
    Container class for player data
    pid: Unique identifier for players
    sock: sock.sock object for connection to player's client
    x: Horizontal position of player
    y: Vertical position of player
    team: Team association of player ("red" or "blue")
    red: Boolean showing if the player is carrying red flag
    blue: Boolean showing if the player is carrying blue flag
    '''
    pid: int
    sock: socket.socket
    x: float
    y: float
    team: str
    red: bool = False
    blue: bool = False

@dataclass
class Flag:
    '''
    Container class for flag data
    team: Team association of the flag ("red" or "blue")
    spawn_x: Horizontal initialization position of the flag
    spawn_y: Vertical initialization position of the flag
    x: Horizontal position of the flag
    y: Vertical position of the flag
    carrier: pid of player carrying the flag
    lock_obj: Mutex lock for thread safe operations
    '''
    team: str
    spawn_x: float
    spawn_y: float
    x: float = field(init=False)
    y: float = field(init=False)
    carrier: Optional[int] = None
    lock_obj: Lock = field(default_factory=Lock)

    #Initialize position to spawn position
    def __post_init__(self):
        self.x = self.spawn_x
        self.y = self.spawn_y

@dataclass
class Game_State:
    '''
    Container class for the current game state
    players: Dictionary of players indexed by pid
    flags: Dictionary of flags indexed by team association
    scores: Dictionary of scores indexed by team association
    map_width: Horizontal pixel size of map
    map_height: Horizontal pixel size of map
    '''
    players: Dict[int, Player] = field(default_factory=dict)
    flags: Dict[str, Flag] = field(default_factory=dict)
    scores: Dict[str, int] = field(default_factory=lambda: {"red": 0, "blue": 0})
    map_width: int = MAP_WIDTH
    map_height: int = MAP_HEIGHT

def send_json(sock: socket.socket, obj: dict):
    '''
    Sends JSON-Based message over desired socket
    sock: socket.socket object over which the json will be sent
    obj: JSON message to be sent
    '''

    try:
        msg = json.dumps(obj) + "\n"
        sock.sendall(msg.encode("utf-8"))
    except Exception as e:
        print(f"[send_json] send error: {e}")

class Server:
    '''
    Server-side represenation of the game state. Responsible for athoritative book-keeping of player objects, flag objects,
    scores, aswell as recieving player inputs/requests, and making decisions on requests
    self.host: Host IP address
    self.port: Host port
    self.listener: socket.socket object for listening for client requests
    self.game: Current game state
    self.next_player_id: pid to be given to next connected player
    self.client_sockets: Dictionary of socket.socket objects for each connected client. Indexed by pid
    self.input_queues: Dictionary of queues for housing client messages. Indexed by pid
    self.client_threads: Dictionary of thread objects for each client. Indexed by pid
    self.struct_lock: Mutex lock for thread-safe operations
    self.running: Up-status of the server
    '''

    def __init__(self, host=HOST, port=PORT):

        #Initialize host and por
        self.host = host
        self.port = port

        #Initialize server socket, configure socket, bind socket to address and port, and begin listening
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind((self.host, self.port))
        self.listener.listen()

        #Initialize game-state and flags
        self.game = Game_State()
        self._init_flags__()

        #Initialize class variables
        self.next_player_id = 1
        self.client_sockets: Dict[int, socket.socket] = {}
        self.input_queues: Dict[int, Queue] = {}
        self.client_threads: Dict[int, threading.Thread] = {}
        self.struct_lock = threading.Lock()
        self.running = True

    
    def _init_flags__(self):
        '''
        Creates and initializes flag objects to be uses in game
        '''
        
        #Find initial positions for flags
        left_x = 50
        right_x = self.game.map_width - 50
        mid_y = self.game.map_height / 2
        #Create flags and add to class's flags
        self.game.flags["red"] = Flag(team="red", spawn_x=left_x, spawn_y=mid_y)
        self.game.flags["blue"] = Flag(team="blue", spawn_x=right_x, spawn_y=mid_y)
    
    def start(self):
        '''
        Function to start server operations
        ''' 

        #Begin listening for clients with accept loop
        t = threading.Thread(target=self.accept, daemon=True)
        t.start()

        #Attempt to begin main game loop
        try:
            self.game_loop()
        except KeyboardInterrupt:
            self.running = False
            self.shutdown()
    
    def accept(self):
        '''
        Function utilized by client threads to accept connections from new clients
        '''
        
        while self.running:

            try:

                #Get client and allow for blocking
                client_sock, addr = self.listener.accept()
                client_sock.setblocking(True)

                #Use server mutex and initialize player
                with self.struct_lock:
                        
                        #Assign player ID
                        pid = self.next_player_id
                        self.next_player_id += 1

                        #Calculate player team and start position
                        team = "red" if pid % 2 == 1 else "blue"
                        spawn_x = 120 if team == "red" else self.game.map_width - 120
                        spawn_y = 100 + (pid * 37) % (self.game.map_height - 200)

                        #Create new player object
                        player = Player(
                            pid=pid,
                            sock=client_sock,
                            x=spawn_x,
                            y=spawn_y,
                            team=team,
                            red = False,
                            blue = False
                        )

                        #Initialize new-client overhead
                        self.game.players[pid] = player
                        self.client_sockets[pid] = client_sock
                        self.input_queues[pid] = Queue()

                        #Create thread for client connection running client's listener
                        th = threading.Thread(target=self.client_listener, args=(pid, client_sock, self.input_queues[pid]), daemon=True)
                        th.start()
                        self.client_threads[pid] = th

                        #Send conncetion message to client
                        send_json(client_sock, {"type": "connect", "pid": pid, "team": team})

            except Exception as e:
                if self.running:
                    print(f"[accept] exception: {e}")
                time.sleep(0.05)

    def client_listener(self, pid: int, sock: socket.socket, q: Queue):
        '''
        Function to be run by client threads to listen for JSON messages
        pid: Player identifier of thread/client
        sock: socket.socket object containing connection to client
        q: Client message queue for storage of non-urgent messages
        '''
        
        #Initialize buffer
        buffer = ""

        try:
            while self.running:
                
                #Recieve data
                try: data = sock.recv(BUFFER_SIZE)

                #If connection broken, disconnect
                except Exception as e:

                    q.put({"type": "disconnect"})
                    break

                if not data:

                    q.put({"type": "disconnect"})
                    break
                
                #Get all sent messaged
                buffer += data.decode("utf-8")

                #If message is in buffer
                while "\n" in buffer:
                    
                    #Split by newline delimitor
                    line, buffer = buffer.split("\n", 1)
                    msg = json.loads(line)

                    #Check if message is urgent (pickup or steal) and run appropriate handler
                    mtype = msg.get("type")
                    if mtype == "pickup":
                        self.attempt_pickup(pid)
                    elif mtype == "steal":
                        target_pid = msg.get("target")
                        flag_team = msg.get("flag")
                        self.attempt_steal(attacker_pid=pid, defender_pid=target_pid, flag_team=flag_team)
                    #Add to queue if not urgent
                    else:
                        q.put(msg)

        except Exception as e:
            q.put({"type": "disconnect"})
            
    
    def game_loop(self):
        '''
        Main game loop, runs all server update functions
        '''
        
        #Get interval between ticks
        interval = 1/TICK_RATE
        last = time.time()

        while self.running:

            now = time.time()
            elapsed = now - last

            #Run server functions
            self.process_inputs()
            self.update_flags()
            self.check_score()

            #Prepare and send update message
            world = self.serialize()
            self.broadcast(world)

            #Sleep until next tick
            last = now
            to_sleep = interval - (time.time() - now)
            if to_sleep > 0:
                time.sleep(to_sleep)

    
    def process_inputs(self):
        '''
        Function to process non-vital messages from clients
        '''
        
        with self.struct_lock: pids = list(self.input_queues.keys())

        #For each player handle input queues
        for pid in pids:
            
            #Get player message queue
            q = self.input_queues.get(pid)
            if q is None:
                continue
            
            #For every message in queue
            while True:
                
                #Get queued message, stop loop when empty
                try:
                    msg = q.get_nowait()
                except Empty:
                    break
                
                #Handle message
                try:
                    self.handle_inputs(pid, msg)
                except Exception as e:
                    print(f"[process_inputs] error handling input for {pid}: {e}")
    
    def handle_inputs(self, pid: int, message: dict):
        '''
        Function for handling all non-vital message types from clients
        pid: Player identifier for sender
        message: JSON message from sender
        '''
        
        #Get sender's player object
        player = self.game.players.get(pid)
        if player is None: return
        
        #Get message's type field
        mtype = message.get("type")

        #If input type, update player position
        if mtype == "input":  
            
            #Get players pressed keys
            move = message.get("move", {})
            UP = move.get("up")
            DOWN = move.get("down")
            LEFT = move.get("left")
            RIGHT = move.get("right")

            #Change position of player based on key-presses
            speed = 6.0
            if UP and not DOWN:
                player.y -= speed

            if DOWN and not UP:
                player.y += speed

            if LEFT and not RIGHT:
                player.x -= speed

            if RIGHT and not LEFT:
                player.x += speed

            #Throttle when at end of map
            player.x = max(0, min(self.game.map_width, player.x))
            player.y = max(0, min(self.game.map_height, player.y))

    
    def attempt_pickup(self, pid: int):
        '''
        Handles client's pickup requests. Uses mutex to ensure multiple simultanious requests
        are not all accepted
        pid: Player identifier of requester
        '''

        #Get requester's player object
        player = self.game.players.get(pid)
        if player is None: return

        #Check all flags for conditions
        for flag in self.game.flags.values():
            
            #Ensure player doesnt pick up own flag
            if flag.team == player.team: continue

            #Trigger mutex lock
            with flag.lock_obj:
                
                #Ensure flag is not carried
                if flag.carrier is not None: continue

                #Check flag is within pickup radius
                dx = player.x - flag.x
                dy = player.y - flag.y

                if dx*dx + dy*dy <= PICKUP_RADIUS*PICKUP_RADIUS:
                    
                    #Assign flag to requester
                    player.red = False
                    player.blue = False
                    flag.carrier = pid

                    if flag.team == "red": player.red = True
                    else: player.blue = True
    
    def update_flags(self):
        '''
        Function to update flags, particularly if a score has occured
        '''
        
        #Check all flags
        for flag in self.game.flags.values():
            
            #If flag is held
            if flag.carrier is not None:

                carrier = self.game.players.get(flag.carrier)

                #Reset position to spawn
                if carrier is None:

                    flag.carrier = None
                    flag.x = flag.spawn_x
                    flag.y = flag.spawn_y

                else:
                    
                    flag.x = carrier.x
                    flag.y = carrier.y
    
    def attempt_steal(self, attacker_pid, defender_pid, flag_team):
        '''
        Handles client's steal requests. Uses mutex to ensure multiple simultanious requests
        are not all accepted
        attacker_pid: Player identifier of requester
        defender_pid: Player identifier for victim of steal
        flag_team: Colour of the flag which is being stolen
        '''

        #Check flag exists
        flag = self.game.flags.get(flag_team)
        if not flag: return

        #Use mutex lock to ensure multiple requests may not be accepted
        with flag.lock_obj:

            #Get relevant player objects
            attacker = self.game.players.get(attacker_pid)
            defender = self.game.players.get(defender_pid)
            if not attacker or not defender: return
            
            #Check players are not on the same team, and run randomness check
            if (flag_team == "red" and not defender.red) or (flag_team == "blue" and not defender.blue): return
            if attacker.team == defender.team: return
            if random.random() >= STEAL_CHANCE: return

            #Swap flag between players
            defender.red = False
            defender.blue = False
            attacker.red = flag_team == "red"
            attacker.blue = flag_team == "blue"
            flag.carrier = attacker.pid
            flag.x = attacker.x
            flag.y = attacker.y

    def check_score(self):
        '''
        Function to check if any players meet the scoring conditions of holdng the opposing flag, and being within
        home-base
        '''
        for pid, player in list(self.game.players.items()):
            
            #Get a list of flags being carried
            carried_flags = []
            if player.red and player.team != "red": carried_flags.append("red")
            if player.blue and player.team != "blue": carried_flags.append("blue")

            if not carried_flags: continue

            #Get flag and associated base
            base_flag = "red" if player.team == "red" else "blue"
            base_x = self.game.flags[base_flag].spawn_x
            base_y = self.game.flags[base_flag].spawn_y

            #Check if flag is within the base
            dx = player.x - base_x
            dy = player.y - base_y
            if dx*dx + dy*dy <= BASE_RADIUS * BASE_RADIUS:

                #Increment current score for scoring team
                scoring_team = player.team

                self.game.scores[scoring_team] += len(carried_flags)

                #Reset flag states
                for f in self.game.flags.values():
                    f.carrier = None
                    f.locked = False
                    f.x = f.spawn_x
                    f.y = f.spawn_y

                for p in self.game.players.values():
                    p.red = False
                    p.blue = False

                #Check if winning score, if so transmit over message
                if self.game.scores[scoring_team] >= SCORE_TO_WIN:
                    self.broadcast({"type": "over", "winner": scoring_team})
                    self.running = False

                return
    
    def serialize(self):
        '''
        Translates current game-state into a serialized JSON message for tranmission as per
        message scheme
        '''

        #Create the player-list field for the message from all active player
        players = []
        for pid, p in self.game.players.items():


            players.append({
                "pid": pid,
                "x": p.x,
                "y": p.y,
                "team": p.team,
                "red": p.red,
                "blue": p.blue,
            })

        #Create the flag-list field for the message from all flags
        flags = []
        for fname, f in self.game.flags.items():

            flags.append({
                "team": f.team,
                "colour": f.team,
                "x": f.x,
                "y": f.y,
                "carrier": f.carrier,
            })

        #Return the serialize world-state update message
        return {
            "type": "update",
            "players": players,
            "flags": flags,
            "scores": self.game.scores
        }
    
    def broadcast(self, obj:Dict):
        '''
        Sends a server message to all clients connected to the server
        obj: JSON message to be transmitted
        '''

        with self.struct_lock:

            #Get all sockets
            items = list(self.client_sockets.items())

        #Send json to all sockets
        for pid, sock in items:
            
            try:
                send_json(sock, obj)
            except Exception as e:
                self.remove_player(pid)
    
    def remove_player(self, pid: int):
        '''
        Removes connection to player and player objects from game-state
        pid: Player identifier of player to be removed
        '''
        
        with self.struct_lock:

            #Get and remove all data associated to player
            sock = self.client_sockets.pop(pid, None)
            q = self.input_queues.pop(pid, None)
            th = self.client_threads.pop(pid, None)
            player = self.game.players.pop(pid, None)

        #Close players socket
        if sock:

            try:
                sock.close()
            except:
                pass
        
        #if player exists, reset all carried flags
        if player:

            if player.red:

                f = self.game.flags.get("red")
                if f:
                    f.carrier = None
                    f.locked = False
                    f.x = f.spawn_x
                    f.y = f.spawn_y

            if player.blue:

                f = self.game.flags.get("blue")
                if f:
                    f.carrier = None
                    f.locked = False
                    f.x = f.spawn_x
                    f.y = f.spawn_y
    
    def shutdown(self):
        '''
        Handles server shutdown even
        '''
        with self.struct_lock:

            #Close all sockets
            for sock in list(self.client_sockets.values()):

                try:
                    sock.close()
                except:
                    pass
            
            #Clear any unprocessed data
            self.client_sockets.clear()
            self.input_queues.clear()
            self.client_threads.clear()

        #Stop listening
        try:
            self.listener.close()
        except:
            pass

if __name__ == "__main__":
    random.seed()  # seed PRNG
    srv = Server(HOST, PORT)
    srv.start()

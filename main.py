import json
import socket
import sys
import math
import Queue
from time import sleep

class F1Bot(object):

    def __init__(self, socket, name, key):
        global join_track_name, traffic_count
        self.socket = socket
        self.name = name
        self.key = key
        self.end = False
        self.colour = None
        self.crash_count = 0
        self.max_crash_count = 10

        self.test_race = False # on commit set to False
        if join_track_name and len(join_track_name) > 0:
            self.join_track_name = join_track_name
        else:
            self.join_track_name = "germany"
            #self.join_track_name = "france"
            #self.join_track_name = "usa"
            #self.join_track_name = "keimola"
        self.traffic_count = traffic_count

        self.lane = 0
        self.track_length = 0
        self.cur_throttle = 0.0
        self.curve_pieces = []
        self.lap_data = []
        self.cur_lap = 0
        self.in_switch = False
        self.switch_lane_index = 0
        self.switch_lane_piece = 0
        self.safe_velocities = {}
        
        self.ticks = 0
        self.time = 0.0
        self.distance = 0.0
        self.velocity = 0.0
        self.acceleration = 0.0
        self.co_friction = 102.0
        self.gravity_constant = 9.867
        self.in_turbo = False
        self.turbo_available = False
        self.in_crash = False
        self.drift_angle = 0

        self.brk_velocity = 5.0
        self.brk_distance = 5.0

        self.f_motion = None
        self.f_track = None

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        if self.f_motion:
            self.f_motion.close()
        if self.f_track:
            self.f_track.close()
        return

    def msg(self, msg_type, data):
        tmp = {"msgType": msg_type, "data": data}
        if self.ticks:
            tmp["gameTick"] = self.ticks
        self.send(json.dumps(tmp))

    def send(self, msg):
        if self.socket:
            self.socket.send(msg + "\n")

    def join(self):
        if self.test_race:
            import random
            self.name = self.name + str(random.randint(1,50))
            data = { "botId": {"name": self.name, "key": self.key}, "trackName": self.join_track_name }
            data["carCount"] = self.traffic_count
            return self.msg("joinRace", data)
        else:
            data = {"name": self.name, "key": self.key}
            return self.msg("join", data)

    def throttle(self, throttle):
        if throttle > 1.0: throttle = 1.0
        if throttle < 0.0: throttle = 0.0
        self.cur_throttle = throttle
        self.msg("throttle", throttle)

    def ping(self):
        self.msg("ping", {})
        
    def switch_lane(self, direction):
        self.msg("switchLane", direction)
        self.in_switch = True
        print("Switching lane to {0}".format(direction))
        pass
        
    def turbo(self):
        self.msg("turbo", "Wwwooooorrrrrrroooooooommmmmm.......")
        pass

    def run(self):
        self.join()
        self.msg_loop()
        
    def on_join(self, data):
        self.ping()
        
    def on_car_init(self,data):
        self.colour = data['color']
        print("Car colour: {0}".format(self.colour))
        pass

    def on_game_start(self, data):
        print("Race started")
        self.ping()
        
    def on_gameInit(self, data):
        self.race = data['race']
        self.track_id = self.race['track']['id']
        self.track_name = self.race['track']['name']
        
        self.cars = self.race["cars"]
        self.my_car = self.get_car(self.cars)
        self.length = self.my_car['dimensions']['length']
        self.width = self.my_car['dimensions']['width']
        self.guideFlagPosition = self.my_car['dimensions']['guideFlagPosition']
        
        self.track_pieces = self.race['track']['pieces']
        self.lanes = self.race['track']['lanes']
        self.startPosition = self.race['track']['startingPoint']
        
        self.curve_pieces = []
        self.switch_pieces = {}
        self.track_length = 0
        cnt = 0
        for x in self.track_pieces:
            x['id'] = cnt
            self.track_length = self.track_length + self.get_piece_length(x)
            if x.has_key('switch') and bool(x['switch']):
                i = cnt + 1
                while i < len(self.track_pieces):
                    p = self.track_pieces[i]
                    if p.has_key('angle'):
                        angle = p['angle']
                        l = self.get_piece_length(p)
                        if angle > 0:
                            self.switch_pieces[cnt] = { "dir": "Right", "sent": False, "curve_idx": i, "angle": angle }
                        elif angle < -30:
                            self.switch_pieces[cnt] = { "dir": "Left", "sent": False, "curve_idx": i, "angle": angle }
                        break
                    i += 1

            if x.has_key('angle') and x.has_key('radius'):                
                self.curve_pieces.append(cnt)
            cnt += 1            

        print("Track: {0}".format(self.track_name))
        print("Track length: {0}".format(self.track_length))
        print("Pieces: {0}".format(len(self.track_pieces)))
        print("Lanes: {0}".format(len(self.lanes)))
        print("Cars: {0}".format(len(self.cars)))
        print("StartingPoint: {0}".format(self.startPosition))
        print("RaceSession: {0}".format(self.race['raceSession']))
        print("Switch Pieces: {0}".format(self.switch_pieces))

        if self.test_race:
            self.f_track = open(self.track_name + ".txt", 'w')
            self.f_track.write(json.dumps(data))
            self.f_motion = open(self.track_name + "_motion.txt","w")
        pass
        
    def get_car(self,data):
        return [x for x in data if x['id']['name'] == self.name][0]

    def learn(self,data):
        #60 ticks/sec
        time = self.ticks / 60.0
        if time == 0 or (time - self.time) == 0: return

        car = self.get_car(data)        
        angle = float(car['angle'])
        idx = int(car['piecePosition']['pieceIndex'])
        lap = int(car['piecePosition']['lap'])
        self.lane = int(car['piecePosition']['lane']['startLaneIndex'])
        endLaneIndex = int(car['piecePosition']['lane']['endLaneIndex'])
        
        distance = self.get_distance_traveled(car)
        velocity = abs((distance - self.distance) / (time - self.time))
        if velocity > self.velocity*1.2:
            #print("Invalid V:{0:.2f} D:{1:.2f}".format(velocity, (distance - self.distance)))
            velocity = self.velocity + 10

        acc = (velocity - self.velocity) / (time - self.time)

        if self.velocity - velocity < 0:# and int(self.cur_throttle) == 0:
            self.brk_velocity = abs(velocity - self.velocity)
            self.brk_distance = abs(distance - self.distance)

        self.distance = distance
        self.acceleration = acc
        self.velocity = velocity
        self.time = time

        if self.test_race:
            if self.in_turbo: turbo = 1 
            else: turbo = 0
            self.f_motion.write("{0},{1:.2f},{2},{3:.2f},{4:.2f},{5:.2f},{6:.2f},{7:.2f},{8}".format(self.ticks, self.time, idx, angle, self.distance, self.velocity, self.acceleration, self.cur_throttle, turbo))
            self.f_motion.write("\n")
        pass

    def get_distance_traveled(self,car):
        idx = int(car['piecePosition']['pieceIndex'])
        pos = float(car['piecePosition']['inPieceDistance'])
        lap = int(car['piecePosition']['lap'])

        distance = self.track_length * lap
        i = 0
        while i < idx:
            piece = self.track_pieces[i]
            length = self.get_piece_length(piece)
            distance += length
            i += 1

        distance += pos
        return distance

    def get_lane_width(self, piece):
        lane = self.lane
        if piece.has_key('lane'):
            lane = piece['lane']
        if self.lanes:
            for l in self.lanes:
                if lane == int(l['index']):
                    return float(l['distanceFromCenter'])
        return 0.0

    def get_piece_length(self, piece):
        if piece.has_key("angle"):
            lane_width = self.get_lane_width(piece)
            angle = float(piece["angle"])
            radius = float(piece["radius"]) - lane_width
            arc_length = (math.pi * radius * abs(angle)) / 180.0
            return arc_length
        elif piece.has_key("length"):
            return float(piece["length"])
        return 0.0

    def get_safe_velocity(self, piece):
        if piece.has_key("radius"):
            idx = piece['id']
            if self.safe_velocities.has_key(idx):
                return self.safe_velocities[idx]
            lane_width = self.get_lane_width(piece)
            radius = float(piece["radius"]) - lane_width
            safe_velocity = math.sqrt(self.co_friction * self.gravity_constant * radius) # sqrt(u*g*R)
            return safe_velocity
        return 1000.0

    def can_activate_turbo(self, data):
        if self.turbo_available and not self.in_turbo:
            car = self.get_car(data)
            idx = int(car['piecePosition']['pieceIndex'])
            # next piece is curve
            if ((idx + 1) in self.curve_pieces):
                p = self.track_pieces[idx+1]
                sv = self.get_safe_velocity(p)
                if self.velocity > sv*1.2:
                    return False            
            return True
        return False

    def on_car_positions(self, data):
        if self.in_crash: self.ping()
        
        self.learn(data) #learn vital info about velocity,acc,time, drift, slip etc.,

        direction = self.can_switch_lane(data)
        if direction:
            self.switch_lane(direction)
            return

        t = self.detect_crash(data)
        if t != -1:
            self.throttle(t)
            return

        self.drive(data)
        
        if self.f_track:
            self.f_track.write('\n' + json.dumps(data))
        pass

    def can_switch_lane(self,data):
        car = self.get_car(data)
        idx = int(car['piecePosition']['pieceIndex'])
        lane_idx = int(car['piecePosition']['lane']['startLaneIndex'])
        end_idx = int(car['piecePosition']['lane']['endLaneIndex'])
        piece = self.track_pieces[idx]
        curr_lane = self.lanes[lane_idx]

        if lane_idx != end_idx:
            #print("S: {0} E: {1}".format(lane_idx,end_idx))
            piece['lane'] = end_idx

        idx += 1
        if self.switch_pieces.has_key(idx) and not self.switch_pieces[idx]["sent"]:
            self.switch_pieces[idx]["sent"] = True
            dir = self.switch_pieces[idx]["dir"]
            cidx = self.switch_pieces[idx]["curve_idx"]
            if idx > cidx: 
                print("Invalid switch piece index")
                return None
            if dir == "Right" and lane_idx != len(self.lanes) - 1: # and curr_lane["distanceFromCenter"] < 0:
                return dir
            elif dir == "Left" and lane_idx != 0: #and curr_lane["distanceFromCenter"] > 0:
                return dir
        return None
  
    def drive(self,data):        
        car = self.get_car(data)
        pos = float(car['piecePosition']['inPieceDistance'])
        drift_angle = abs(float(car['angle']))
        idx = int(car['piecePosition']['pieceIndex'])
        distance = float(car['piecePosition']['inPieceDistance'])
        piece = self.track_pieces[idx]
        piece_length = self.get_piece_length(piece)
        #safe_dis = piece_length*0.6
        #if piece.has_key('angle'): safe_dis = piece_length*0.25
        #if self.in_turbo: safe_dis = piece_length*0.05
        
        pda = self.drift_angle
        self.drift_angle = drift_angle
        
        i = idx + 1 
        clen = piece_length - distance
        while i < len(self.track_pieces) and i < idx+3:
            p = self.track_pieces[i]
            if p.has_key('angle'):
                radius = p['radius']
                pl = self.get_piece_length(p)
                clen += (pl * 0.20)
                sv = self.get_safe_velocity(p)
                if self.velocity > sv and self.brk_velocity > 0 and self.brk_distance > 0:
                    dv = self.velocity - sv / self.brk_velocity
                    dd = clen / self.brk_distance
                    if drift_angle > 40:
                        print("Drift: {0:.1f} R: {1} Throttle: {2:.1f} Vs: {3:.2f} V: {4:.2f} u:{5:.2f}".format(drift_angle, radius, self.cur_throttle, sv, self.velocity, self.co_friction))
                    t = ((dv/dd)/100.0) + (drift_angle/100.0)
                    t = self.cur_throttle - t
                    #if t <= 0: t = 0.1
                    self.throttle(t) #slow down
                    return
            i += 1
            clen += self.get_piece_length(p)
        
        if self.can_activate_turbo(data):
            self.turbo()
        else:
            if drift_angle > pda:
                self.throttle(0.5) #push the metal
            else:
                self.throttle(1.0) #push the metal
                    
    def detect_crash(self,data):
        car = self.get_car(data)
        my_pos = float(car['piecePosition']['inPieceDistance'])
        my_lane = int(car['piecePosition']['lane']['endLaneIndex'])

        for c in data:
            if c == car:
                continue
            else:
                o_pos = float(c['piecePosition']['inPieceDistance'])
                o_lane = int(c['piecePosition']['lane']['endLaneIndex'])
                if my_lane == o_lane and o_pos > my_pos and (o_pos - my_pos) < (self.length * 0.5):
                    return 1 - (o_pos - my_pos)/10.0
        return -1
                
    def on_crash(self, data):
        print("Crashed: {0} ; Count: {1}".format(data, self.crash_count))
        self.cur_throttle = 0.0
        self.in_crash = True
        self.ping()
        if self.crash_count >= self.max_crash_count:
            print('Exiting race due to max crashes! :(')
            self.end = True
        else:
            self.crash_count = self.crash_count + 1        
        
    def on_spawn(self,data):
        print("Spawning...")
        self.in_crash = False
        self.throttle(1.0)
        
    def on_lapFinish(self,data):
        #print("LapFinish: {0}".format(data))
        self.ping()
        if data['car']['color'] != self.colour:
            return
        lap  = int(data['lapTime']['lap'])
        lapTime = float(data['lapTime']['millis'])/1000.0
        d, t = self.distance, self.time
        if len(self.lap_data) > 0:
            for l in self.lap_data:
                d = d - l[0]
                t = t - l[1]
        self.lap_data.append((d, t))
        print("Lap: {0} Time: {1:.2f} Distance: {2:.2f}".format(lap, lapTime, d))
        self.reset_switch_pieces()

    def reset_switch_pieces(self):
        for k in self.switch_pieces:
            self.switch_pieces[k]["sent"] = False
        return
        
    def on_finish(self,data):
        print("Finish Race: {0}".format(data))
        self.ping()
        
    def on_dnf(self,data):
        print("Disqualified: {0}".format(data))
        self.ping()
        self.end = True

    def on_turboAvailable(self,data):
        if not self.turbo_available:
            self.turbo_available = True
        self.ping()

    def on_turboStart(self,data):
        self.in_turbo = True
        self.turbo_available = False
        self.ping()
        print("Wwwwwoooorrrooommmm.......")

    def on_turboEnd(self,data):
        self.in_turbo = False
        self.turbo_available = False
        self.ping()
        print("Zzzzzzzzz.......")
        
    def on_game_end(self, data):
        print("Race ended")
        #print("Race ended: {0}".format(data))
        self.ping()

    def on_error(self, data):
        print("Error: {0}".format(data))
        self.ping()
        self.end = True

    def on_tournamentEnd(self, data):
        print("Tournament Ended")
        self.ping()
        self.end = True
        return

    def msg_loop(self):
        msg_map = {
            'join': self.on_join,
            'yourCar': self.on_car_init,
            'gameStart': self.on_game_start,
            'gameInit': self.on_gameInit,
            'carPositions': self.on_car_positions,
            'crash': self.on_crash,
            'spawn': self.on_spawn,
            'turboAvailable': self.on_turboAvailable,
            'turboStart': self.on_turboStart,
            'turboEnd': self.on_turboEnd,
            'lapFinished': self.on_lapFinish,
            'dnf': self.on_dnf,
            'error': self.on_error,
            'finish': self.on_finish,
            'gameEnd': self.on_game_end,
            "tournamentEnd": self.on_tournamentEnd,
        }
        socket_file = self.socket.makefile()
        line = socket_file.readline()
        while line:
            msg = json.loads(line)
            msg_type, data = msg['msgType'], msg['data']
            if msg.has_key("gameTick"):
                self.ticks = int(msg["gameTick"])
            if msg_type in msg_map:
                msg_map[msg_type](data)
            else:
                print("Got {0}. Data: {1}".format(msg_type, data))
                self.ping()
            line = socket_file.readline()

def run_bot(host, port, name, key, test):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((host, int(port)))
    with F1Bot(s, name, key) as bot:
        bot.test_race = test
        bot.run()
    s.close()
        
join_track_name = None
traffic_count = 1
if __name__ == "__main__":
    test = False
    if len(sys.argv) != 5:
        print("Usage: ./run host port botname botkey")
        host = 'testserver.helloworldopen.com'
        #host = 'hakkinen.helloworldopen.com'
        port = 8091
        name = 'F1-Calculus'
        key = 'joNEvgGvOL8GRQ'
        test = True

        if len(sys.argv) > 1:
            join_track_name = sys.argv[1]
        if len(sys.argv) > 2:
            traffic_count = int(sys.argv[2])
    else:
        host, port, name, key = sys.argv[1:5]
    print("Connecting with parameters:")
    print("host={0}, port={1}, bot name={2}, key={3}".format(host,port,name,key))
    run_bot(host, port, name, key, test)

import socket
import threading
import json
import math
import random
import time
import os
import argparse
import logging

HOST = "0.0.0.0"
PORT = 5555

UNIT_SCORES = {
    "Peasant": 1,
    "Archer": 2,
    "Rider": 2,
    "Medic": 2,
    "Shieldman": 2,
    "Knight": 3,
    "King": 5,
    "Catapult": 3
}

UNIT_WEIGHTS = {
    "Peasant": 1.0,
    "Archer": 0.8,
    "Rider": 3, # Increased from 0.9 to trample infantry
    "Medic": 0.8,
    "King": 5.5,
    "Knight": 3,
    "Shieldman": 3.5,
    "Catapult": 3,
}

def setup_logging(verbose):
    log_level = logging.DEBUG if verbose else logging.INFO
    log_format = "%(asctime)s [%(levelname)s] %(message)s"

    logger = logging.getLogger()
    logger.setLevel(log_level)

    if logger.hasHandlers():
        logger.handlers.clear()

    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(logging.Formatter(log_format))
    logger.addHandler(console_handler)

def generate_noise_grid(grid_w, grid_h):
    return [[random.uniform(-1.0, 1.0) for _ in range(grid_h + 1)] for _ in range(grid_w + 1)]

def sample_smooth_noise(grid, x, y):
    gx, gy = int(x), int(y)
    fx, fy = x - gx, y - gy

    fade = lambda t: t * t * (3 - 2 * t)
    sx, sy = fade(fx), fade(fy)

    v00 = grid[gx][gy]
    v10 = grid[gx + 1][gy]
    v01 = grid[gx][gy + 1]
    v11 = grid[gx + 1][gy + 1]

    top = v00 + sx * (v10 - v00)
    bottom = v01 + sx * (v11 - v01)
    return top + sy * (bottom - top)

def find_nearest_enemy_in_path(unit, enemies, scan_radius=None, max_leash=None):
    if scan_radius is None:
        scan_radius = unit["radius"] * 6.0
    if max_leash is None:
        max_leash = unit["radius"] * 7.0

    closest_enemy = None
    min_dist = scan_radius

    guard_x = unit.get("guard_x", unit["x"])
    guard_y = unit.get("guard_y", unit["y"])

    for enemy in enemies:
        if enemy["hp"] <= 0:
            continue

        dx = enemy["x"] - unit["x"]
        dy = enemy["y"] - unit["y"]
        dist = math.hypot(dx, dy)

        dist_from_guard = math.hypot(enemy["x"] - guard_x, enemy["y"] - guard_y)

        if dist < min_dist and dist_from_guard <= max_leash:
            min_dist = dist
            closest_enemy = enemy

    return closest_enemy

def find_nearest_hurt_ally(unit, allies, scan_radius=None, max_leash=None):
    if scan_radius is None:
        scan_radius = unit["radius"] * 10.0
    if max_leash is None:
        max_leash = unit["radius"] * 8.0

    closest_ally = None
    min_dist = scan_radius

    guard_x = unit.get("guard_x", unit["x"])
    guard_y = unit.get("guard_y", unit["y"])

    for ally in allies:
        if ally["id"] == unit["id"] or ally["hp"] >= ally["max_hp"] or ally["hp"] <= 0:
            continue

        dx = ally["x"] - unit["x"]
        dy = ally["y"] - unit["y"]
        dist = math.hypot(dx, dy)

        dist_from_guard = math.hypot(ally["x"] - guard_x, ally["y"] - guard_y)

        if dist < min_dist and dist_from_guard <= max_leash:
            min_dist = dist
            closest_ally = ally

    return closest_ally

def generate_heightmap(size, water_enabled, units=None, water_rising=False):
    logging.debug(f"Generating heightmap for size {size}")
    grid = [[0.0 for _ in range(size)] for _ in range(size)]

    octave1 = generate_noise_grid(4, 4)
    octave2 = generate_noise_grid(8, 8)
    octave3 = generate_noise_grid(16, 16)

    cx, cy = size / 2.0, size / 2.0
    tile_pixel_size = 800.0 / size

    base_bias = random.uniform(-0.6, 0.4)

    for r in range(size):
        for c in range(size):
            nx, ny = c / float(size), r / float(size)
            val1 = sample_smooth_noise(octave1, nx * 4, ny * 4) * 1.0
            val2 = sample_smooth_noise(octave2, nx * 8, ny * 8) * 0.5
            val3 = sample_smooth_noise(octave3, nx * 16, ny * 16) * 0.25

            elevation = val1 + val2 + val3 + base_bias

            if water_enabled:
                dist_center = math.hypot(c - cx, r - cy) / (size * 0.5)
                elevation -= max(0.0, 0.4 - dist_center * 0.3)

            grid[r][c] = round(elevation, 2)

    if water_enabled or water_rising:
        peak_r, peak_c = int(size * 0.5), int(size * 0.5)
        highest_val = 3.5
        peak_radius = size * 0.35

        for r in range(size):
            for c in range(size):
                dist = math.hypot(c - peak_c, r - peak_r)
                if dist <= peak_radius:
                    blend = dist / peak_radius
                    smooth = 1.0 - (blend * blend * (3 - 2 * blend))
                    grid[r][c] = max(grid[r][c], grid[r][c] + smooth * highest_val)

    if units is not None:
        protected_positions = [(u["x"], u["y"]) for u in units if u["type"] == "King"]
    else:
        protected_positions = [(400, 680), (400, 120), (120, 400), (680, 400)]

    for wx, wy in protected_positions:
        center_c = int(wx / tile_pixel_size)
        center_r = int(wy / tile_pixel_size)
        island_radius = 12.0
        target_land_height = 0.2

        for r in range(max(0, int(center_r - island_radius)), min(size, int(center_r + island_radius + 1))):
            for c in range(max(0, int(center_c - island_radius)), min(size, int(center_c + island_radius + 1))):
                dist = math.hypot(c - center_c, r - center_r)
                if dist <= island_radius:
                    factor = 1.0 - (dist / island_radius)
                    smooth = factor * factor * (3.0 - 2.0 * factor)
                    if grid[r][c] < target_land_height:
                        grid[r][c] = max(grid[r][c], grid[r][c] * (1.0 - smooth) + target_land_height * smooth)
    return grid

def get_height_at_pos(wx, wy, heightmap, board_size):
    if not heightmap:
        return 0.0
    tile_size = 800.0 / board_size

    gx = max(0.0, min(board_size - 1.001, wx / tile_size))
    gy = max(0.0, min(board_size - 1.001, wy / tile_size))

    x0, y0 = int(gx), int(gy)
    x1, y1 = min(board_size - 1, x0 + 1), min(board_size - 1, y0 + 1)

    fx, fy = gx - x0, gy - y0

    fx = fx * fx * (3.0 - 2.0 * fx)
    fy = fy * fy * (3.0 - 2.0 * fy)

    h00 = heightmap[y0][x0]
    h10 = heightmap[y0][x1]
    h01 = heightmap[y1][x0]
    h11 = heightmap[y1][x1]

    top = h00 + fx * (h10 - h00)
    bottom = h01 + fx * (h11 - h01)
    return top + fy * (bottom - top)

def get_height_modifier(current_pos, next_pos, heightmap, board_size):
    h_curr = get_height_at_pos(current_pos[0], current_pos[1], heightmap, board_size)
    h_next = get_height_at_pos(next_pos[0], next_pos[1], heightmap, board_size)
    return h_next - h_curr

def has_cone_vision(viewer, target_x, target_y):
    if viewer["type"] in ("Archer", "Rider", "Catapult"):
        max_range = viewer["radius"] * 28.0
    else:
        max_range = viewer["radius"] * 12.0
    dx = target_x - viewer["x"]
    dy = target_y - viewer["y"]
    dist = math.hypot(dx, dy)
    return dist <= max_range

def is_in_front_arc(viewer, target_x, target_y, max_angle_radians=math.pi / 2):
    dx = target_x - viewer["x"]
    dy = target_y - viewer["y"]
    target_angle = math.atan2(dy, dx)
    angle_diff = (target_angle - viewer["angle"] + math.pi) % (2 * math.pi) - math.pi
    return abs(angle_diff) <= max_angle_radians

def lerp_angle(current, target, max_delta):
    diff = (target - current + math.pi) % (2 * math.pi) - math.pi
    if diff > max_delta:
        return current + max_delta
    elif diff < -max_delta:
        return current - max_delta
    return target

def get_unit_base_speed(unit_type):
    if unit_type == "Rider":
        return 3.2
    elif unit_type == "King":
        return 1.4
    elif unit_type in ("Knight", "Shieldman"):
        return 1.0
    elif unit_type == "Catapult":
        return 0.9  # Adjusted walking/movement speed for the catapult
    return 2.2

class Server:
    def __init__(self):
        logging.info("Initializing Game Server...")
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((HOST, PORT))
        self.sock.listen(4)
        self.usernames = {}

        self.clients = {}
        self.host_id = None
        self.scores = {i: 0 for i in range(4)}
        self.kills = {i: 0 for i in range(4)}
        self.board_size = 32
        self.fog_enabled = False
        self.water_rising_enabled = False
        self.water_enabled = True
        self.game_mode = "FFA"
        self.win_condition = "LAST_MAN_STANDING"
        self.target_score = 3

        self.units = []
        self.projectiles = []

        self.heightmap = generate_heightmap(self.board_size, self.water_enabled, units=self.units, water_rising=self.water_rising_enabled)
        self.water_level = -0.1

        self.starting_gold = 2000
        self.state = "LOBBY"
        self.projectiles = []
        self.gold = {}
        self.ready = {}
        self.next_unit_id = 1
        self.lock = threading.RLock()
        logging.info(f"Server initialized successfully on board size {self.board_size}x{self.board_size}")

    def update_host(self):
        if self.clients:
            if self.host_id not in self.clients:
                self.host_id = min(self.clients.keys())
        else:
            self.host_id = None

    def broadcast(self, msg):
        data = (json.dumps(msg) + "\n").encode('utf-8')
        with self.lock:
            for player_id, conn in list(self.clients.items()):
                try:
                    conn.sendall(data)
                except Exception as e:
                    logging.error(f"Failed to broadcast message to player {player_id}: {e}")
                    if player_id in self.clients:
                        del self.clients[player_id]

                    self.update_host()
                    self.state = "LOBBY"
                    self.units = []
                    logging.warning(f"Player {player_id} dropped mid-game. Ending match.")
                    try:
                        conn.sendall((json.dumps({
                            "type": "PLAYER_DISCONNECT",
                            "disconnected_id": player_id,
                            "scores": self.scores,
                            "kills": self.kills,
                            "host_id": self.host_id
                        }) + "\n").encode('utf-8'))
                    except:
                        pass

    def award_kill_score(self, killer_id, unit_type):
        points = UNIT_SCORES.get(unit_type, 1)
        if self.game_mode == "2v2":
            for p in self.kills:
                if (p % 2) == (killer_id % 2):
                    self.kills[p] += points
        else:
            if killer_id in self.kills:
                self.kills[killer_id] += points

    def award_win(self, winner_id):
        if winner_id is None or winner_id < 0:
            return
        if self.game_mode == "2v2":
            for p in self.scores:
                if (p % 2) == (winner_id % 2):
                    self.scores[p] += 1
        else:
            if winner_id in self.scores:
                self.scores[winner_id] += 1

    def handle_client(self, player_id, conn):
        logging.info(f"Handling connection for Player {player_id}")
        conn.sendall((json.dumps({
            "type": "INIT",
            "player_id": player_id,
            "host_id": self.host_id,
            "scores": self.scores,
            "kills": self.kills,
            "usernames": self.usernames,
            "board_size": self.board_size,
            "heightmap": self.heightmap,
            "water_level": self.water_level,
            "starting_gold": self.starting_gold,
            "game_mode": self.game_mode,
            "win_condition": self.win_condition,
            "target_score": self.target_score,
            "fog_enabled": self.fog_enabled
        }) + "\n").encode('utf-8'))

        buffer = ""
        while True:
            try:
                data = conn.recv(4096).decode('utf-8')
                if not data:
                    break
                buffer += data
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    msg = json.loads(line)
                    self.process_message(player_id, msg)
            except Exception as e:
                logging.debug(f"Exception in client handler for Player {player_id}: {e}")
                break

        with self.lock:
            if player_id in self.clients:
                del self.clients[player_id]
                logging.info(f"Player {player_id} disconnected and removed from clients list.")
            if player_id in self.usernames:
                del self.usernames[player_id]
            self.update_host()
            self.broadcast({
                "type": "USERNAMES_UPDATE",
                "usernames": self.usernames,
                "host_id": self.host_id
            })

    def process_message(self, pid, msg):
        mtype = msg.get("type")
        if mtype == "SET_NAME":
            name = msg.get("username", f"Player {pid + 1}").strip()
            if name:
                self.usernames[pid] = name
                self.broadcast({
                    "type": "USERNAMES_UPDATE",
                    "usernames": self.usernames,
                    "host_id": self.host_id
                })
                self.broadcast({"type": "CHAT", "sender": "System", "text": f"{name} joined the game!"})

        elif mtype == "CHAT":
            sender_name = self.usernames.get(pid, f"Player {pid + 1}")
            self.broadcast({"type": "CHAT", "sender": sender_name, "text": msg["text"]})

        elif mtype == "SELL_UNIT" and self.state == "SHOP":
            uid = msg.get("unit_id")
            for i, u in enumerate(self.units):
                if u["id"] == uid and u["owner"] == pid and u["type"] != "King":
                    costs = {"Peasant": 100, "Archer": 150, "Rider": 140, "Medic": 180, "Shieldman": 120, "Knight": 250, "Catapult": 300}
                    self.gold[pid] += costs.get(u["type"], 0)
                    self.units.pop(i)

                    self.broadcast({
                        "type": "SHOP_UPDATE",
                        "units": self.units,
                        "gold": self.gold,
                        "ready": self.ready,
                        "heightmap": self.heightmap
                    })
                    break

        elif mtype == "TOGGLE_WIN_CONDITION" and pid == self.host_id and self.state == "LOBBY":
            self.win_condition = "MOST_SCORE_AT_END" if self.win_condition == "LAST_MAN_STANDING" else "LAST_MAN_STANDING"
            self.broadcast({
                "type": "SETTINGS_UPDATE",
                "win_condition": self.win_condition,
                "target_score": self.target_score,
                "game_mode": self.game_mode,
                "water_rising": self.water_rising_enabled,
                "fog_enabled": self.fog_enabled,
                "heightmap": self.heightmap
            })

        elif mtype == "TOGGLE_FOG" and pid == self.host_id and self.state == "LOBBY":
            self.fog_enabled = not self.fog_enabled
            self.broadcast({
                "type": "SETTINGS_UPDATE",
                "fog_enabled": self.fog_enabled,
                "win_condition": self.win_condition,
                "target_score": self.target_score,
                "game_mode": self.game_mode,
                "water_rising": self.water_rising_enabled,
                "heightmap": self.heightmap
            })

        elif mtype == "SET_TARGET_SCORE" and pid == self.host_id and self.state == "LOBBY":
            self.target_score = max(1, min(20, msg.get("target_score", 3)))
            self.broadcast({
                "type": "SETTINGS_UPDATE",
                "win_condition": self.win_condition,
                "target_score": self.target_score,
                "game_mode": self.game_mode,
                "water_rising": self.water_rising_enabled,
                "fog_enabled": self.fog_enabled,
                "heightmap": self.heightmap
            })

        elif mtype == "SET_BOARD_SIZE" and pid == self.host_id and self.state == "LOBBY":
            self.board_size = max(12, min(128, msg["size"]))
            self.heightmap = generate_heightmap(self.board_size, self.water_enabled, water_rising=self.water_rising_enabled)
            self.broadcast({"type": "BOARD_SIZE", "size": self.board_size, "heightmap": self.heightmap})

        elif mtype == "SET_WATER_RISING" and pid == self.host_id and self.state == "LOBBY":
            self.water_rising_enabled = msg["rising"]
            self.heightmap = generate_heightmap(self.board_size, self.water_enabled, water_rising=self.water_rising_enabled)
            self.broadcast({"type": "SETTINGS_UPDATE", "water_rising": self.water_rising_enabled, "fog_enabled": self.fog_enabled, "heightmap": self.heightmap})

        elif mtype == "SET_STARTING_GOLD" and pid == self.host_id and self.state == "LOBBY":
            self.starting_gold = max(100, min(10000, msg["starting_gold"]))
            self.broadcast({"type": "GOLD_SETTING_UPDATE", "starting_gold": self.starting_gold})

        elif mtype == "TOGGLE_MODE" and pid == self.host_id and self.state == "LOBBY":
            self.game_mode = "2v2" if self.game_mode == "FFA" else "FFA"
            self.broadcast({"type": "SETTINGS_UPDATE", "game_mode": self.game_mode, "win_condition": self.win_condition, "target_score": self.target_score, "fog_enabled": self.fog_enabled})

        elif mtype == "START_GAME" and pid == self.host_id and self.state == "LOBBY":
            logging.info("Host started the game. Transitioning to SHOP phase.")
            self.state = "SHOP"
            self.water_level = -0.1 if self.water_enabled else -99.0
            self.units = []
            self.kills = {i: 0 for i in range(4)}

            tile_pixel_size = 800.0 / self.board_size
            four_block_radius = int(2.0 * tile_pixel_size) / 2
            king_positions = {
                0: (400, 680, 3*math.pi/2),
                1: (400, 120, math.pi/2),
                2: (120, 400, 0.0),
                3: (680, 400, math.pi)
            }

            connected_players = list(self.clients.keys())
            for p in connected_players:
                kx, ky, kang = king_positions.get(p, (400, 400, 0.0))
                self.units.append({
                    "id": self.next_unit_id,
                    "owner": p,
                    "type": "King",
                    "shape": "octagon",
                    "x": kx,
                    "y": ky,
                    "target_x": kx,
                    "target_y": ky,
                    "guard_x": kx,
                    "guard_y": ky,
                    "waypoints": [],
                    "hp": 300,
                    "max_hp": 300,
                    "angle": kang,
                    "is_moving": False,
                    "is_hit": False,
                    "last_attack": 0,
                    "target_unit": None,
                    "draw_radius": int(tile_pixel_size * 1.5),
                    "radius": four_block_radius,
                    "vx": 0.0,
                    "vy": 0.0,
                    "group_speed": None
                })
                self.next_unit_id += 1

            self.heightmap = generate_heightmap(self.board_size, self.water_enabled, units=self.units, water_rising=self.water_rising_enabled)
            self.gold = {}

            if self.game_mode == "2v2":
                team_counts = {
                    0: sum(1 for p in connected_players if p % 2 == 0),
                    1: sum(1 for p in connected_players if p % 2 == 1)
                }
                for p in connected_players:
                    my_team = p % 2
                    opp_team = 1 - my_team
                    if team_counts[my_team] == 1 and team_counts[opp_team] == 2:
                        self.gold[p] = int(self.starting_gold * 2.0)
                    else:
                        self.gold[p] = self.starting_gold
            else:
                for p in connected_players:
                    self.gold[p] = self.starting_gold
            self.ready = {p: False for p in connected_players}

            self.broadcast({
                "type": "SHOP_START",
                "board_size": self.board_size,
                "heightmap": self.heightmap,
                "water_level": self.water_level,
                "units": self.units,
                "gold": self.gold,
                "kills": self.kills
            })

        elif mtype == "BUY_UNIT" and self.state == "SHOP":
            costs = {"Peasant": 100, "Archer": 150, "Rider": 140, "Medic": 180, "Shieldman": 120, "Knight": 250, "Catapult": 300}
            utype = msg["unit_type"]
            cost = costs.get(utype, 100)

            if self.gold.get(pid, 0) >= cost and "x" in msg and "y" in msg:
                sPeasant_x, sPeasant_y = msg["x"], msg["y"]
                if not (0 <= sPeasant_x <= 800 and 0 <= sPeasant_y <= 800):
                    return

                valid_side = True
                if pid == 0 and sPeasant_y < 400: valid_side = False
                elif pid == 1 and sPeasant_y > 400: valid_side = False
                elif pid == 2 and sPeasant_x > 400: valid_side = False
                elif pid == 3 and sPeasant_x < 400: valid_side = False

                if not valid_side:
                    return

                terrain_height = get_height_at_pos(sPeasant_x, sPeasant_y, self.heightmap, self.board_size)
                if terrain_height <= self.water_level:
                    return

                self.gold[pid] -= cost

                shapes = {
                    "Peasant": "circle", "Knight": "square", "Archer": "pentagon", "Catapult": "square",
                    "Rider": "triangle", "Medic": "cross", "Shieldman": "hexagon"
                }
                max_hps = {"Peasant": 70, "Archer": 20, "Rider": 75, "Medic": 90, "Shieldman": 250, "Knight": 200, "Catapult": 30}

                tile_pixel_size = 800.0 / self.board_size
                radius_multipliers = {
                    "Peasant": 0.9,
                    "Archer": 1.0,
                    "Rider": 1.1,
                    "Medic": 1.0,
                    "Shieldman": 1.2,
                    "Knight": 1.2,
                    "Catapult": 1.4
                }
                four_block_radius = (int(2.0 * tile_pixel_size) / 2) * radius_multipliers.get(utype, 1.0)
                draw_radii = {
                    "Peasant": int(tile_pixel_size * 1.2), "Archer": int(tile_pixel_size * 1.4),
                    "Rider": int(tile_pixel_size * 1.3), "Medic": int(tile_pixel_size * 1.3),
                    "Shieldman": int(tile_pixel_size * 1.4), "Knight": int(tile_pixel_size * 1.3),
                    "Catapult": int(tile_pixel_size * 1.5)
                }

                self.units.append({
                    "id": self.next_unit_id,
                    "owner": pid,
                    "type": utype,
                    "shape": shapes[utype],
                    "x": sPeasant_x,
                    "y": sPeasant_y,
                    "target_x": sPeasant_x,
                    "target_y": sPeasant_y,
                    "guard_x": sPeasant_x,
                    "guard_y": sPeasant_y,
                    "waypoints": [],
                    "hp": max_hps[utype],
                    "max_hp": max_hps[utype],
                    "angle": 0.0,
                    "is_moving": False,
                    "is_hit": False,
                    "last_attack": 0,
                    "target_unit": None,
                    "draw_radius": draw_radii[utype],
                    "radius": four_block_radius,
                    "vx": 0.0,
                    "vy": 0.0,
                    "group_speed": None
                })
                self.next_unit_id += 1

                self.broadcast({
                    "type": "SHOP_UPDATE",
                    "units": self.units,
                    "gold": self.gold,
                    "ready": self.ready,
                    "heightmap": self.heightmap
                })

        elif mtype == "READY_SHOP" and self.state == "SHOP":
            self.ready[pid] = True
            self.broadcast({"type": "SHOP_UPDATE", "units": self.units, "gold": self.gold, "ready": self.ready})
            if all(self.ready.values()) or len(self.clients) == 1:
                self.state = "IN_GAME"
                self.broadcast({
                    "type": "GAME_START",
                    "units": self.units,
                    "board_size": self.board_size,
                    "heightmap": self.heightmap,
                    "water_level": self.water_level,
                    "kills": self.kills
                })

        elif mtype == "COMMAND" and self.state == "IN_GAME":
            u_ids = msg.get("unit_ids", [])
            tx, ty = msg["target_pos"]
            t_unit = msg.get("target_unit")
            append_path = msg.get("append_path", False)
            selected_group = [u for u in self.units if u["owner"] == pid and u["id"] in u_ids]

            if selected_group:
                center_x = sum(u["x"] for u in selected_group) / len(selected_group)
                center_y = sum(u["y"] for u in selected_group) / len(selected_group)

                group_min_speed = min(get_unit_base_speed(u["type"]) for u in selected_group) if len(selected_group) > 1 else None

                for u in selected_group:
                    valid_t_unit = None
                    ignore_command = False
                    if t_unit is not None:
                        t_obj = next((e for e in self.units if e["id"] == t_unit), None)
                        if t_obj:
                            if u["type"] == "Medic" and not self.is_enemy(u["owner"], t_obj["owner"]):
                                valid_t_unit = t_unit
                            elif u["type"] != "Medic" and self.is_enemy(u["owner"], t_obj["owner"]):
                                valid_t_unit = t_unit
                            # If neither is met, valid_t_unit remains None and ignores the unit target.

                    if valid_t_unit is not None:
                        bounded_tx = tx
                        bounded_ty = ty
                    else:
                        offset_x = u["x"] - center_x
                        offset_y = u["y"] - center_y
                        bounded_tx = max(u["radius"], min(800.0 - u["radius"], tx + offset_x))
                        bounded_ty = max(u["radius"], min(800.0 - u["radius"], ty + offset_y))

                    wp = (bounded_tx, bounded_ty, valid_t_unit)

                    if "waypoints" not in u:
                        u["waypoints"] = []

                    u["group_speed"] = group_min_speed if len(selected_group) > 1 else None
                    u["guard_x"] = bounded_tx
                    u["guard_y"] = bounded_ty

                    if not append_path:
                        u["waypoints"] = [wp]
                        u["target_unit"] = t_unit
                        u["target_x"], u["target_y"] = wp[0], wp[1]
                    else:
                        u["waypoints"].append(wp)
                        if len(u["waypoints"]) == 1:
                            u["target_unit"] = t_unit
                            u["target_x"], u["target_y"] = wp[0], wp[1]

    def is_enemy(self, owner1, owner2):
        if self.game_mode == "2v2":
            return (owner1 % 2) != (owner2 % 2)
        return owner1 != owner2

    def game_loop(self):
        has_had_clients = False
        while True:
            time.sleep(0.033)

            with self.lock:
                if len(self.clients) > 0:
                    has_had_clients = True
                if has_had_clients and self.state != "LOBBY" and len(self.clients) == 0:
                    os._exit(0)

            if self.state != "IN_GAME":
                continue

            if self.water_enabled and self.water_rising_enabled:
                self.water_level += 0.0001



            for _ in range(3):
                for i in range(len(self.units)):
                    for j in range(i + 1, len(self.units)):
                        u1 = self.units[i]
                        u2 = self.units[j]
                        dx = u2["x"] - u1["x"]
                        dy = u2["y"] - u1["y"]
                        dist = math.hypot(dx, dy)
                        min_dist = u1["radius"] + u2["radius"]

                        if 0 < dist < min_dist:
                            overlap = min_dist - dist
                            nx = dx / dist
                            ny = dy / dist

                            w1 = UNIT_WEIGHTS.get(u1["type"], 1.0)
                            w2 = UNIT_WEIGHTS.get(u2["type"], 1.0)
                            total_weight = w1 + w2

                            ratio1 = w2 / total_weight
                            ratio2 = w1 / total_weight

                            u1["x"] -= nx * (overlap * ratio1)
                            u1["y"] -= ny * (overlap * ratio1)
                            u2["x"] += nx * (overlap * ratio2)
                            u2["y"] += ny * (overlap * ratio2)

            for u in self.units:
                u["is_hit"] = False
                target = None

                current_h = get_height_at_pos(u["x"], u["y"], self.heightmap, self.board_size)
                water_depth = max(0.0, self.water_level - current_h)

                if water_depth > 0:
                    u["hp"] -= water_depth * 0.8
                    u["is_hit"] = True

                if u.get("waypoints"):
                    current_wp = u["waypoints"][0]
                    wp_x, wp_y = current_wp[0], current_wp[1]
                    wp_target_id = current_wp[2] if len(current_wp) > 2 else None

                    if wp_target_id is not None:
                        t_exists = any(e["id"] == wp_target_id and e["hp"] > 0 for e in self.units)
                        if t_exists:
                            u["target_unit"] = wp_target_id
                        else:
                            u["target_unit"] = None
                            u["waypoints"][0] = (wp_x, wp_y, None)
                            u["target_x"], u["target_y"] = wp_x, wp_y
                    else:
                        u["target_unit"] = None
                        u["target_x"], u["target_y"] = wp_x, wp_y

                if u["type"] == "Medic":
                    if u.get("target_unit"):
                        target = next((e for e in self.units if e["id"] == u["target_unit"] and not self.is_enemy(e["owner"], u["owner"])), None)
                        if target and target["hp"] >= target["max_hp"]:
                            u["target_unit"] = None
                            target = None

                    if not target and not u.get("target_unit"):
                        friendlies = [e for e in self.units if not self.is_enemy(e["owner"], u["owner"]) and e["hp"] < e["max_hp"] and e["id"] != u["id"]]
                        scan_range = u["radius"] * 10.0
                        leash_range = u["radius"] * 8.0
                        target = find_nearest_hurt_ally(u, friendlies, scan_radius=scan_range, max_leash=leash_range)
                        if target:
                            u["target_unit"] = target["id"]
                        elif not u.get("waypoints"):
                            enemies = [e for e in self.units if self.is_enemy(e["owner"], u["owner"])]
                            nearby_enemy = find_nearest_enemy_in_path(u, enemies, scan_radius=u["radius"] * 12.0, max_leash=9999.0)
                            if not nearby_enemy:
                                u["target_x"] = u.get("guard_x", u["x"])
                                u["target_y"] = u.get("guard_y", u["y"])

                    # --- ADD THIS BLOCK TO FIX MEDIC PATHING ---
                    if target and u.get("target_unit"):
                        is_move_order = u.get("waypoints") and len(u["waypoints"]) > 0 and u["waypoints"][0][2] is None
                        if not is_move_order:
                            u["target_x"] = target["x"]
                            u["target_y"] = target["y"]
                    # -------------------------------------------


                elif u["type"] != "Shieldman":
                    if u.get("target_unit"):
                        target = next((e for e in self.units if e["id"] == u["target_unit"] and self.is_enemy(e["owner"], u["owner"])), None)

                        if target:
                            explicit_target = u.get("waypoints") and len(u["waypoints"]) > 0 and u["waypoints"][0][2] == target["id"]
                            if not explicit_target:
                                guard_dist = math.hypot(target["x"] - u.get("guard_x", u["x"]), target["y"] - u.get("guard_y", u["y"]))
                                max_leash = u["radius"] * 30.0 if u["type"] in ("Archer", "Catapult") else u["radius"] * 7.0
                                if guard_dist > max_leash:
                                    target = None
                                    u["target_unit"] = None

                            # Override explicit target if an immediate threat is in the way
                            if target:
                                enemies = [e for e in self.units if self.is_enemy(e["owner"], u["owner"])]
                                scan_range = u["radius"] * 18.0 if u["type"] == "Archer" else u["radius"] * 6.0
                                closest_enemy = find_nearest_enemy_in_path(u, enemies, scan_radius=scan_range, max_leash=scan_range)

                                if closest_enemy and closest_enemy["id"] != target["id"]:
                                    target = closest_enemy
                                    u["target_unit"] = target["id"]

                    if not target:
                        enemies = [e for e in self.units if self.is_enemy(e["owner"], u["owner"])]

                    # Replace the duplicated blocks with this:
                    if not target and u["type"] != "Medic":
                        enemies = [e for e in self.units if self.is_enemy(e["owner"], u["owner"])]
                        scan_range = u["radius"] * 18.0 if u["type"] in ("Archer", "Catapult") else u["radius"] * 6.0
                        leash_range = u["radius"] * 20.0 if u["type"] in ("Archer", "Catapult") else u["radius"] * 7.0

                        closest_enemy = find_nearest_enemy_in_path(u, enemies, scan_radius=scan_range, max_leash=leash_range)
                        if closest_enemy:
                            target = closest_enemy
                            u["target_unit"] = target["id"]
                        elif not u.get("waypoints"):
                            any_enemy_near = find_nearest_enemy_in_path(u, enemies, scan_radius=scan_range, max_leash=u["radius"] * 3.0)
                            if not any_enemy_near:
                                u["target_x"] = u.get("guard_x", u["x"])
                                u["target_y"] = u.get("guard_y", u["y"])

                    if u["type"] in ("Archer", "Catapult"):
                        if not u.get("waypoints"):
                            u["target_x"] = u["x"]
                            u["target_y"] = u["y"]
                            u["is_moving"] = False

                    elif target and u.get("target_unit"):
                        # Check if the unit is currently following a manual move-only command
                        is_move_order = u.get("waypoints") and len(u["waypoints"]) > 0 and u["waypoints"][0][2] is None

                        # Only divert pathing to chase if they aren't retreating/repositioning
                        if not is_move_order:
                            u["target_x"] = target["x"]
                            u["target_y"] = target["y"]

                dx = u["target_x"] - u["x"]
                dy = u["target_y"] - u["y"]
                dist = math.hypot(dx, dy)

                stop_distance = 3.0
                if target and u.get("target_unit"):
                    stop_distance = u["radius"] + target["radius"] + 2.0

                if dist > stop_distance:
                    desired_angle = math.atan2(dy, dx)
                    if u["type"] == "Catapult" and time.time() - u.get("last_attack", 0) < 2.5:
                        u["is_moving"] = False
                        u["vx"] = 0.0
                        u["vy"] = 0.0
                        continue

                    turn_speed = 0.25 if u["type"] == "Catapult" else 0.5
                    u["angle"] = lerp_angle(u["angle"], desired_angle, turn_speed)

                    if u["type"] == "Catapult":
                        angle_diff = abs((desired_angle - u["angle"] + math.pi) % (2 * math.pi) - math.pi)
                        if angle_diff > 0.5:
                            u["is_moving"] = True
                            u["vx"] *= 0.5
                            u["vy"] *= 0.5
                                                # Allow slow forward crawl while turning instead of hard stopping

                    u["is_moving"] = True

                    tile_pixel_size = 800.0 / self.board_size
                    base_blocks_per_second = u.get("group_speed") if u.get("group_speed") is not None else get_unit_base_speed(u["type"])
                    speed = base_blocks_per_second * tile_pixel_size * 0.1

                    step_x_dir = dx / dist
                    step_y_dir = dy / dist
                    look_ahead_x = u["x"] + step_x_dir * 10.0
                    look_ahead_y = u["y"] + step_y_dir * 10.0

                    height_diff = get_height_modifier((u["x"], u["y"]), (look_ahead_x, look_ahead_y), self.heightmap, self.board_size)

                    speed_multiplier = max(0.4, min(1.8, 1.0 - (height_diff * 0.5)))
                    speed *= speed_multiplier

                    if water_depth > 0:
                        speed *= max(0.15, 1.0 - (water_depth * 0.75))

                    target_vx = step_x_dir * speed
                    target_vy = step_y_dir * speed

                    u["vx"] = u.get("vx", 0.0) * 0.7 + target_vx * 0.3
                    u["vy"] = u.get("vy", 0.0) * 0.7 + target_vy * 0.3

                    u["x"] += u["vx"]
                    u["y"] += u["vy"]

                    if not target or not has_cone_vision(u, target["x"], target["y"]):
                        if u["type"] != "Catapult":
                            u["angle"] = lerp_angle(u["angle"], desired_angle, 0.5)
                else:
                    u["is_moving"] = False
                    u["vx"] = 0.0
                    u["vy"] = 0.0

                    # Smoothly turn towards target enemy when stationary
                    if target:
                        desired_angle = math.atan2(target["y"] - u["y"], target["x"] - u["x"])
                        u["angle"] = lerp_angle(u["angle"], desired_angle, 0.02)

                    if u.get("waypoints"):
                        current_wp = u["waypoints"][0]
                        wp_target_id = current_wp[2] if len(current_wp) > 2 else None

                        if wp_target_id is None:
                            u["waypoints"].pop(0)
                            if u["waypoints"]:
                                next_wp = u["waypoints"][0]
                                u["target_x"], u["target_y"] = next_wp[0], next_wp[1]
                                u["target_unit"] = next_wp[2] if len(next_wp) > 2 else None
                            else:
                                u["group_speed"] = None

                if target and not u["is_moving"]:
                    desired_angle = math.atan2(target["y"] - u["y"], target["x"] - u["x"])
                    u["angle"] = lerp_angle(u["angle"], desired_angle, 0.15)
                    e_dist = math.hypot(target["x"] - u["x"], target["y"] - u["y"])
                    can_see = not self.fog_enabled or has_cone_vision(u, target["x"], target["y"])
                    in_front = is_in_front_arc(u, target["x"], target["y"])

                    # Define 'now' here so every unit type can access it
                    now = time.time()

                    if u["type"] == "Archer":
                        archer_range = u["radius"] * 18.0
                        projectile_speed = u["radius"] * 0.3
                        projectile_life = max(1, int(archer_range / projectile_speed))
                        if not u["is_moving"] and e_dist < archer_range and can_see and in_front and now - u["last_attack"] > 1.2:
                            base_ang = math.atan2(target["y"] - u["y"], target["x"] - u["x"])
                            u["angle"] = lerp_angle(u["angle"], base_ang, 0.1)

                            if abs((base_ang - u["angle"] + math.pi) % (2 * math.pi) - math.pi) < 0.2:
                                u["last_attack"] = now
                                target_h = get_height_at_pos(target["x"], target["y"], self.heightmap, self.board_size)
                                attacker_h = get_height_at_pos(u["x"], u["y"], self.heightmap, self.board_size)
                                h_diff = target_h - attacker_h
                                dmg_mult = max(0.3, min(2.0, 1.0 - (h_diff * 0.4)))

                                archer_range = u["radius"] * 18.0
                                projectile_speed = u["radius"] * 0.45
                                projectile_life = max(1, int(archer_range / projectile_speed))

                                vx = math.cos(base_ang) * projectile_speed
                                vy = math.sin(base_ang) * projectile_speed

                                self.projectiles.append({
                                    "type": "Archer",
                                    "x": u["x"], "y": u["y"],
                                    "vx": vx,
                                    "vy": vy,
                                    "owner": u["owner"],
                                    "damage": 15 * dmg_mult,
                                    "life": projectile_life,
                                    "angle": base_ang
                                })
                                self.broadcast({"type": "ATTACK_SOUND", "unit_type": "Archer"})


                    vx, vy = 0.0, 0.0

                    if u["type"] == "Catapult":
                        dx = target["x"] - u["x"]
                        dy = target["y"] - u["y"]
                        dist = math.hypot(dx, dy)
                        projectile_speed = 6.0
                        if dist > 0:
                            vx = (dx / dist) * projectile_speed
                            vy = (dy / dist) * projectile_speed
                        catapult_range = u["radius"] * 35.0
                        splash_radius = u["radius"] * 4.5
                        projectile_speed = u["radius"] * 0.05
                        projectile_life = max(1, int(catapult_range/11))

                        if not u["is_moving"] and e_dist < catapult_range and can_see and in_front:
                            base_ang = math.atan2(target["y"] - u["y"], target["x"] - u["x"])
                            u["angle"] = lerp_angle(u["angle"], base_ang, 0.001)

                            # Fire only when aligned
                            now = time.time()
                            if abs((base_ang - u["angle"] + math.pi) % (2 * math.pi) - math.pi) < 0.15 and now - u["last_attack"] > 2.0:
                                u["last_attack"] = now
                                self.projectiles.append({
                                    "owner": u["owner"],
                                    "x": u["x"],
                                    "y": u["y"],
                                    "target_x": target["x"],
                                    "target_y": target["y"],
                                    "vx": vx,
                                    "damage": 80,
                                    "life": projectile_life,
                                    "speed": projectile_speed,
                                    "vy": vy,
                                    "type": "Catapult",
                                    "angle": 0.0,
                                    "spin_rate": 0.25  # Speed at which the boulder rotates in flight
                                })
                                self.broadcast({"type": "ATTACK_SOUND", "unit_type": "Catapult"})

                    elif u["type"] == "Medic":
                        heal_range = (u["radius"] + target["radius"]) * 1.2
                        if e_dist < heal_range and in_front and now - u["last_attack"] > 0.8:
                            u["last_attack"] = now
                            target["hp"] = min(target["max_hp"], target["hp"] + 15)
                            target["is_hit"] = True
                            self.broadcast({"type": "ATTACK_SOUND", "unit_type": "Medic"})
                    elif u["type"] != "Shieldman":
                        attack_range = (u["radius"] + target["radius"]) * 1.25
                        cooldown = 0.6 if u["type"] == "Rider" else (1.0 if u["type"] == "King" else 0.8)
                        damage_val = 18 if u["type"] == "Rider" else (30 if u["type"] == "King" else 20)

                        if e_dist < attack_range and can_see and in_front and now - u["last_attack"] > cooldown:
                            u["last_attack"] = now

                            target_h = get_height_at_pos(target["x"], target["y"], self.heightmap, self.board_size)
                            attacker_h = get_height_at_pos(u["x"], u["y"], self.heightmap, self.board_size)
                            h_diff = target_h - attacker_h

                            damage_multiplier = max(0.3, min(2.0, 1.0 - (h_diff * 0.4)))

                            # Add speed-based scaling specifically for the Rider
                            if u["type"] == "Rider":
                                current_speed = math.hypot(u.get("vx", 0.0), u.get("vy", 0.0))
                                # Massive damage for peak velocity; practically harmless if attacking from a standstill
                                speed_factor = max(0.1, min(4.0, (current_speed * 1.8) * u.get("momentum", 1.0)))
                                damage_val *= speed_factor
                            # Increment momentum gradually for long, uninterrupted charges
                                u["momentum"] = min(1.0, u.get("momentum", 0.0) + 0.015)
                                # Sluggish start, rapidly accelerating when fully charged
                                accel = 0.02 + (0.15 * u["momentum"])
                                u["vx"] = u.get("vx", 0.0) * (1.0 - accel) + target_vx * accel
                                u["vy"] = u.get("vy", 0.0) * (1.0 - accel) + target_vy * accel
                            else:
                                u["vx"] = u.get("vx", 0.0) * 0.7 + target_vx * 0.3
                                u["vy"] = u.get("vy", 0.0) * 0.7 + target_vy * 0.3

                            final_damage = damage_val * damage_multiplier
                            target["hp"] -= final_damage

                            target["is_hit"] = True
                            self.broadcast({"type": "ATTACK_SOUND", "unit_type": u["type"]})

                            if target["hp"] <= 0:
                                self.award_kill_score(u["owner"], target["type"])

            alive_projectiles = []
            for p in self.projectiles:
                p["x"] += p["vx"]
                p["y"] += p["vy"]
                if p.get("type") == "Catapult":
                    p["angle"] = p.get("angle", 0.0) + p.get("spin_rate", 0.25)
                p_speed = p.get("radius", 10.0) * (p.get("speed") or 5.0) * 0.5  # Scaled down to reduce projectile speed
                p["life"] -= 1
                hit = False

                for u in self.units:
                    if u["hp"] > 0 and self.is_enemy(u["owner"], p["owner"]):
                        if math.hypot(u["x"] - p["x"], u["y"] - p["y"]) < (u["radius"] + 2):
                            u["hp"] -= p["damage"]
                            u["is_hit"] = True
                            hit = True

                            if u["hp"] <= 0:
                                self.award_kill_score(p["owner"], u["type"])
                            break

                if not hit and p["life"] > 0:
                    alive_projectiles.append(p)

            self.projectiles = alive_projectiles
            dead_kings_owners = [u["owner"] for u in self.units if u["type"] == "King" and u["hp"] <= 0]
            for u in self.units:
                if u["owner"] in dead_kings_owners:
                    u["hp"] = 0
            self.units = [u for u in self.units if u["hp"] > 0]

            alive_teams = set()
            for u in self.units:
                if u["type"] == "King":
                    team = (u["owner"] % 2) if self.game_mode == "2v2" else u["owner"]
                    alive_teams.add(team)

            match_winner = None
            match_over = False

            if self.win_condition == "MOST_SCORE_AT_END":
                for p, k_score in self.kills.items():
                    if k_score >= self.target_score:
                        match_winner = (p % 2) if self.game_mode == "2v2" else p
                        match_over = True
                        break

            if not match_over and len(alive_teams) <= 1:
                round_winner = list(alive_teams)[0] if alive_teams else -1
                if round_winner != -1:
                    match_winner = round_winner
                    match_over = True

            if match_over:
                self.award_win(match_winner)
                self.state = "LOBBY"
                self.projectiles = []
                self.broadcast({
                    "type": "GAME_OVER",
                    "winner": match_winner,
                    "scores": self.scores,
                    "kills": self.kills,
                    "win_condition": self.win_condition
                })
            else:
                self.broadcast({
                    "type": "GAME_UPDATE",
                    "units": self.units,
                    "projectiles": self.projectiles,
                    "water_level": self.water_level,
                    "kills": self.kills
                })

    def run(self):
        threading.Thread(target=self.game_loop, daemon=True).start()
        logging.info(f"Server started and listening on {HOST}:{PORT}")
        while True:
            conn, addr = self.sock.accept()
            logging.info(f"New connection accepted from {addr}")
            with self.lock:
                avail = [i for i in range(4) if i not in self.clients]
                if not avail:
                    conn.close()
                    continue
                current_id = avail[0]
                self.clients[current_id] = conn
                self.usernames[current_id] = f"Player {current_id + 1}"
                self.update_host()
            self.broadcast({
                "type": "USERNAMES_UPDATE",
                "usernames": self.usernames,
                "host_id": self.host_id
            })
            threading.Thread(target=self.handle_client, args=(current_id, conn), daemon=True).start()

if __name__ == "__main__":
    setup_logging(verbose=False)
    Server().run()

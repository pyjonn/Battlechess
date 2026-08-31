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
    "Pawn": 1,
    "Knight": 2,
    "Bishop": 2,
    "Healer": 2,
    "Block": 2,
    "Rook": 3,
    "Queen": 4,
    "King": 5
}

UNIT_WEIGHTS = {
    "Pawn": 1.0,
    "Knight": 0.8,
    "Bishop": 0.9,
    "Healer": 0.8,
    "King": 1.5,
    "Rook": 3,
    "Block": 3.5,
    "Queen": 2.5,
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

def find_nearest_enemy_in_path(unit, enemies, scan_radius=80.0, max_leash=90.0):
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

def find_nearest_hurt_ally(unit, allies, scan_radius=250.0, max_leash=200.0):
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
                    blend = dist / island_radius
                    smooth = blend * blend * (3 - 2 * smooth if 'smooth' in locals() else 3 - 2 * blend)
                    original_h = grid[r][c]
                    if original_h < target_land_height:
                        boosted_h = (target_land_height * (1.0 - smooth)) + (original_h * smooth)
                        grid[r][c] = max(original_h, boosted_h)
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
    max_range = 200.0
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
    if unit_type == "Bishop":
        return 3.2
    elif unit_type == "King":
        return 1.4
    elif unit_type in ("Rook", "Block"):
        return 1.0
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
                    costs = {"Pawn": 100, "Knight": 150, "Bishop": 140, "Healer": 180, "Block": 120, "Rook": 250, "Queen": 400}
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
            costs = {"Pawn": 100, "Knight": 150, "Bishop": 140, "Healer": 180, "Block": 120, "Rook": 250, "Queen": 400}
            utype = msg["unit_type"]
            cost = costs.get(utype, 100)

            if self.gold.get(pid, 0) >= cost and "x" in msg and "y" in msg:
                spawn_x, spawn_y = msg["x"], msg["y"]
                if not (0 <= spawn_x <= 800 and 0 <= spawn_y <= 800):
                    return

                valid_side = True
                if pid == 0 and spawn_y < 400: valid_side = False
                elif pid == 1 and spawn_y > 400: valid_side = False
                elif pid == 2 and spawn_x > 400: valid_side = False
                elif pid == 3 and spawn_x < 400: valid_side = False

                if not valid_side:
                    return

                terrain_height = get_height_at_pos(spawn_x, spawn_y, self.heightmap, self.board_size)
                if terrain_height <= self.water_level:
                    return

                self.gold[pid] -= cost

                shapes = {
                    "Pawn": "circle", "Rook": "square", "Knight": "pentagon",
                    "Queen": "hexagon", "Bishop": "triangle", "Healer": "cross", "Block": "square"
                }
                max_hps = {"Pawn": 70, "Knight": 20, "Bishop": 75, "Healer": 90, "Block": 250, "Rook": 200, "Queen": 300}

                tile_pixel_size = 800.0 / self.board_size
                radius_multipliers = {
                    "Pawn": 0.9,
                    "Knight": 1.0,
                    "Bishop": 1.1,
                    "Healer": 1.0,
                    "Block": 1.2,
                    "Rook": 1.2,
                    "Queen": 1.3
                }
                four_block_radius = (int(2.0 * tile_pixel_size) / 2) * radius_multipliers.get(utype, 1.0)
                draw_radii = {
                    "Pawn": int(tile_pixel_size * 1.2), "Knight": int(tile_pixel_size * 1.4),
                    "Bishop": int(tile_pixel_size * 1.3), "Healer": int(tile_pixel_size * 1.3),
                    "Block": int(tile_pixel_size * 1.4), "Rook": int(tile_pixel_size * 1.3), "Queen": int(tile_pixel_size * 1.4)
                }

                self.units.append({
                    "id": self.next_unit_id,
                    "owner": pid,
                    "type": utype,
                    "shape": shapes[utype],
                    "x": spawn_x,
                    "y": spawn_y,
                    "target_x": spawn_x,
                    "target_y": spawn_y,
                    "guard_x": spawn_x,
                    "guard_y": spawn_y,
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
                    offset_x = u["x"] - center_x
                    offset_y = u["y"] - center_y

                    bounded_tx = max(u["radius"], min(800.0 - u["radius"], tx + offset_x))
                    bounded_ty = max(u["radius"], min(800.0 - u["radius"], ty + offset_y))

                    wp = (bounded_tx, bounded_ty, t_unit)

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

            now = time.time()

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

                if u["type"] == "Healer":
                    if u.get("target_unit"):
                        target = next((e for e in self.units if e["id"] == u["target_unit"] and not self.is_enemy(e["owner"], u["owner"])), None)
                        if target and target["hp"] >= target["max_hp"]:
                            u["target_unit"] = None
                            target = None

                    if not target and not u.get("target_unit"):
                        friendlies = [e for e in self.units if not self.is_enemy(e["owner"], u["owner"]) and e["hp"] < e["max_hp"] and e["id"] != u["id"]]
                        target = find_nearest_hurt_ally(u, friendlies)
                        if target:
                            u["target_unit"] = target["id"]
                        elif not u.get("waypoints"):
                            enemies = [e for e in self.units if self.is_enemy(e["owner"], u["owner"])]
                            nearby_enemy = find_nearest_enemy_in_path(u, enemies, scan_radius=500.0, max_leash=9999.0)
                            if not nearby_enemy:
                                u["target_x"] = u.get("guard_x", u["x"])
                                u["target_y"] = u.get("guard_y", u["y"])

                elif u["type"] != "Block":
                    if u.get("target_unit"):
                        target = next((e for e in self.units if e["id"] == u["target_unit"] and self.is_enemy(e["owner"], u["owner"])), None)

                        if target:
                            guard_dist = math.hypot(target["x"] - u.get("guard_x", u["x"]), target["y"] - u.get("guard_y", u["y"]))
                            max_leash = 150.0 if u["type"] == "Knight" else 120.0
                            if guard_dist > max_leash:
                                target = None
                                u["target_unit"] = None

                    if not target:
                        enemies = [e for e in self.units if self.is_enemy(e["owner"], u["owner"])]
                        scan_range = 140.0 if u["type"] == "Knight" else 100.0
                        leash_range = 150.0 if u["type"] == "Knight" else 120.0

                        closest_enemy = find_nearest_enemy_in_path(u, enemies, scan_radius=scan_range, max_leash=leash_range)
                        if closest_enemy:
                            target = closest_enemy
                            u["target_unit"] = target["id"]
                        elif not u.get("waypoints"):
                            any_enemy_near = find_nearest_enemy_in_path(u, enemies, scan_radius=scan_range, max_leash=50.0)
                            if not any_enemy_near:
                                u["target_x"] = u.get("guard_x", u["x"])
                                u["target_y"] = u.get("guard_y", u["y"])

                    if u["type"] == "Knight" and target:
                        archer_range = 175.0
                        e_dist = math.hypot(target["x"] - u["x"], target["y"] - u["y"])

                        desired_angle = math.atan2(target["y"] - u["y"], target["x"] - u["x"])
                        u["angle"] = lerp_angle(u["angle"], desired_angle, 0.25)

                        if e_dist <= archer_range:
                            u["waypoints"] = []
                            u["target_x"] = u["x"]
                            u["target_y"] = u["y"]
                            u["is_moving"] = False

                    if target and u.get("target_unit"):
                        archer_range = 175.0 if u["type"] == "Knight" else 0.0
                        e_dist = math.hypot(target["x"] - u["x"], target["y"] - u["y"])
                        if u["type"] != "Knight" or e_dist > archer_range:
                            u["target_x"] = target["x"]
                            u["target_y"] = target["y"]

                dx = u["target_x"] - u["x"]
                dy = u["target_y"] - u["y"]
                dist = math.hypot(dx, dy)
                if dist > 3:
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
                        desired_angle = math.atan2(dy, dx)
                        u["angle"] = lerp_angle(u["angle"], desired_angle, 0.15)
                else:
                    u["is_moving"] = False
                    u["vx"] = 0.0
                    u["vy"] = 0.0

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

                if target:
                    desired_angle = math.atan2(target["y"] - u["y"], target["x"] - u["x"])
                    u["angle"] = lerp_angle(u["angle"], desired_angle, 0.25 if not u["is_moving"] else 0.15)
                    e_dist = math.hypot(target["x"] - u["x"], target["y"] - u["y"])
                    can_see = not self.fog_enabled or has_cone_vision(u, target["x"], target["y"])

                    in_front = is_in_front_arc(u, target["x"], target["y"])

                    if u["type"] == "Knight":
                        archer_range = 350.0
                        projectile_life = max(1, int(archer_range / 11.0))

                        if not u["is_moving"] and e_dist < archer_range and can_see and in_front and now - u["last_attack"] > 1.2:
                            u["last_attack"] = now
                            base_ang = math.atan2(target["y"] - u["y"], target["x"] - u["x"])
                            u["angle"] = base_ang

                            target_h = get_height_at_pos(target["x"], target["y"], self.heightmap, self.board_size)
                            attacker_h = get_height_at_pos(u["x"], u["y"], self.heightmap, self.board_size)
                            h_diff = target_h - attacker_h
                            dmg_mult = max(0.3, min(2.0, 1.0 - (h_diff * 0.4)))

                            self.projectiles.append({
                                "x": u["x"], "y": u["y"], "angle": base_ang + random.uniform(-0.1, 0.1),
                                "owner": u["owner"], "damage": 25 * dmg_mult, "life": projectile_life
                            })
                            self.broadcast({"type": "ATTACK_SOUND", "unit_type": "Knight"})
                    elif u["type"] == "Healer":
                        heal_range = (u["radius"] + target["radius"] + 10)
                        if e_dist < heal_range and in_front and now - u["last_attack"] > 0.8:
                            u["last_attack"] = now
                            target["hp"] = min(target["max_hp"], target["hp"] + 15)
                            target["is_hit"] = True
                            self.broadcast({"type": "ATTACK_SOUND", "unit_type": "Healer"})
                    elif u["type"] != "Block":
                        attack_range = (u["radius"] + target["radius"] + 14)
                        cooldown = 0.6 if u["type"] == "Bishop" else (1.0 if u["type"] == "King" else 0.8)
                        damage_val = 18 if u["type"] == "Bishop" else (30 if u["type"] == "King" else 20)

                        if e_dist < attack_range and can_see and in_front and now - u["last_attack"] > cooldown:
                            u["last_attack"] = now

                            target_h = get_height_at_pos(target["x"], target["y"], self.heightmap, self.board_size)
                            attacker_h = get_height_at_pos(u["x"], u["y"], self.heightmap, self.board_size)
                            h_diff = target_h - attacker_h

                            damage_multiplier = max(0.3, min(2.0, 1.0 - (h_diff * 0.4)))
                            final_damage = damage_val * damage_multiplier
                            target["hp"] -= final_damage

                            target["is_hit"] = True
                            self.broadcast({"type": "ATTACK_SOUND", "unit_type": u["type"]})

                            if target["hp"] <= 0:
                                self.award_kill_score(u["owner"], target["type"])

            alive_projectiles = []
            for p in self.projectiles:
                p["x"] += math.cos(p["angle"]) * 11.0
                p["y"] += math.sin(p["angle"]) * 11.0
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
    parser = argparse.ArgumentParser(description="Realtime Chess Game Server")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose debug logging")
    args = parser.parse_args()
    setup_logging(args.verbose)
    Server().run()

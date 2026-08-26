import socket
import threading
import json
import math
import random
import time

HOST = "0.0.0.0"
PORT = 5555

def generate_heightmap(size, water_enabled):
    grid = [[0.0 for _ in range(size)] for _ in range(size)]
    cx, cy = size / 2.0, size / 2.0
    for r in range(size):
        for c in range(size):
            nx, ny = c / float(size), r / float(size)
            # Add random variation alongside smooth wave blending
            h = (math.sin(nx * 3.14 * 4) * math.cos(ny * 3.14 * 4) * 1.0) + random.uniform(-0.3, 0.3)

            if water_enabled:
                dist_from_center = math.hypot(c - cx, r - cy)
                lake_radius = size * 0.18
                if dist_from_center < lake_radius:
                    depth_factor = (1.0 - (dist_from_center / lake_radius)) ** 2
                    h = -0.5 - (depth_factor * 1.5)

            grid[r][c] = round(h, 2)
    return grid
def get_height_at_pos(wx, wy, heightmap, board_size):
    if not heightmap:
        return 0.0
    c = max(0, min(board_size - 1, int(wx / (800.0 / board_size))))
    r = max(0, min(board_size - 1, int(wy / (800.0 / board_size))))
    return heightmap[r][c]

def has_plain_circle_vision(viewer, target_x, target_y):
    max_range = 200.0
    dx = target_x - viewer["x"]
    dy = target_y - viewer["y"]
    return math.hypot(dx, dy) <= max_range

class Server:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((HOST, PORT))
        self.sock.listen(2)

        self.clients = {}
        self.scores = {0: 0, 1: 0}
        self.board_size = 24
        self.fog_enabled = False
        self.water_rising_enabled = False
        self.water_enabled = True  # Set to True so water is generated immediately
        self.heightmap = generate_heightmap(self.board_size, self.water_enabled)
        self.water_level = -0.5

        self.starting_gold = 2000
        self.state = "LOBBY"
        self.units = []
        self.projectiles = []
        self.gold = {0: self.starting_gold, 1: self.starting_gold}
        self.ready = {0: False, 1: False}
        self.next_unit_id = 1
        self.lock = threading.Lock()

    def broadcast(self, msg):
        data = (json.dumps(msg) + "\n").encode('utf-8')
        with self.lock:
            for pid, cl_sock in list(self.clients.items()):
                try:
                    cl_sock.sendall(data)
                except:
                    pass

    def handle_client(self, player_id, conn):
        conn.sendall((json.dumps({
            "type": "INIT",
            "player_id": player_id,
            "scores": self.scores,
            "board_size": self.board_size,
            "heightmap": self.heightmap,
            "water_level": self.water_level,
            "starting_gold": self.starting_gold
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
            except:
                break

        with self.lock:
            if player_id in self.clients:
                del self.clients[player_id]

    def process_message(self, pid, msg):
        mtype = msg.get("type")

        if mtype == "CHAT":
            self.broadcast({"type": "CHAT", "sender": f"Player {pid + 1}", "text": msg["text"]})

        elif mtype == "SET_BOARD_SIZE" and pid == 0 and self.state == "LOBBY":
            self.board_size = msg["size"]
            self.heightmap = generate_heightmap(self.board_size, self.water_enabled)
            self.broadcast({"type": "BOARD_SIZE", "size": self.board_size, "heightmap": self.heightmap})

        elif mtype == "SET_STARTING_GOLD" and pid == 0 and self.state == "LOBBY":
            self.starting_gold = max(500, min(5000, msg["gold"]))
            self.broadcast({"type": "GOLD_SETTING_UPDATE", "starting_gold": self.starting_gold})

        elif mtype == "TOGGLE_FOG" and pid == 0:
            self.fog_enabled = msg["fog"]
            self.broadcast({"type": "SETTINGS_UPDATE", "fog": self.fog_enabled, "water": self.water_enabled})

        elif mtype == "TOGGLE_WATER" and pid == 0:
            self.water_enabled = msg["water"]
            self.heightmap = generate_heightmap(self.board_size, self.water_enabled)
            self.broadcast({
                "type": "SETTINGS_UPDATE",
                "fog": self.fog_enabled,
                "water": self.water_enabled,
                "heightmap": self.heightmap
            })

        elif mtype == "START_GAME" and pid == 0 and self.state == "LOBBY":
            self.state = "SHOP"
            self.water_level = -1.2 if self.water_enabled else -0.5
            self.units = []
            self.gold = {0: self.starting_gold, 1: self.starting_gold}
            self.ready = {0: False, 1: False}
            self.broadcast({
                "type": "SHOP_START",
                "board_size": self.board_size,
                "heightmap": self.heightmap,
                "water_level": self.water_level,
                "units": self.units,
                "gold": self.gold
            })

        elif mtype == "BUY_UNIT" and self.state == "SHOP":
                    costs = {"Pawn": 100, "Knight": 150, "Bishop": 180, "Rook": 250, "Queen": 400}
                    utype = msg["unit_type"]
                    cost = costs.get(utype, 100)
                    if self.gold[pid] >= cost:
                        self.gold[pid] -= cost

                        # Count existing units for this player to calculate a non-overlapping spawn offset
                        player_units = [u for u in self.units if u["owner"] == pid]
                        row_idx = len(player_units) // 6
                        col_idx = len(player_units) % 6

                        base_y = 100 if pid == 0 else 700
                        spawn_x = 200 + (col_idx * 45)
                        spawn_y = base_y + (row_idx * 25 * (1 if pid == 0 else -1))

                        # Requested shape assignments:
                        shapes = {
                            "Pawn": "circle",
                            "Rook": "square",
                            "Knight": "pentagon",
                            "Queen": "hexagon",
                            "Bishop": "octagon"
                        }
                        max_hps = {"Pawn": 100, "Knight": 80, "Bishop": 120, "Rook": 200, "Queen": 250}
                        draw_radii = {"Pawn": 9, "Knight": 11, "Bishop": 11, "Rook": 10, "Queen": 15}
                        collision_radii = {
                            "Pawn": 15, "Knight": 18, "Bishop": 18, "Rook": 16, "Queen": 23
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
                            "hp": max_hps[utype],
                            "max_hp": max_hps[utype],
                            "angle": 0.0,
                            "is_moving": False,
                            "is_hit": False,
                            "last_attack": 0,
                            "target_unit": None,
                            "draw_radius": draw_radii[utype],
                            "radius": collision_radii[utype]
                        })
                        self.next_unit_id += 1
                        self.broadcast({"type": "SHOP_UPDATE", "units": self.units, "gold": self.gold, "ready": self.ready})

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
                    "water_level": self.water_level
                })

        elif mtype == "COMMAND" and self.state == "IN_GAME":
                    u_ids = msg.get("unit_ids", [])
                    tx, ty = msg["target_pos"]
                    t_unit = msg.get("target_unit")

                    # Filter the specific units being commanded
                    selected_group = [u for u in self.units if u["owner"] == pid and u["id"] in u_ids]

                    if selected_group:
                        # 1. Find the current center (average X and Y) of the selected formation
                        center_x = sum(u["x"] for u in selected_group) / len(selected_group)
                        center_y = sum(u["y"] for u in selected_group) / len(selected_group)

                        # 2. Apply the offset from the old center to each unit's individual target position
                        for u in selected_group:
                            offset_x = u["x"] - center_x
                            offset_y = u["y"] - center_y

                            # The new target preserves their exact formation layout, centered on the click (tx, ty)
                            u["target_x"] = tx + offset_x
                            u["target_y"] = ty + offset_y
                            u["target_unit"] = t_unit
    def game_loop(self):
        while True:
            time.sleep(0.033)
            if self.state != "IN_GAME":
                continue

            if self.water_enabled and self.water_rising_enabled:
                self.water_level += 0.001

            now = time.time()

            # Repulsion using tuned smaller collision radii
            for i in range(len(self.units)):
                for j in range(i + 1, len(self.units)):
                    u1 = self.units[i]
                    u2 = self.units[j]
                    dx = u2["x"] - u1["x"]
                    dy = u2["y"] - u1["y"]
                    dist = math.hypot(dx, dy)
                    min_dist = u1["radius"] + u2["radius"]

                    if dist < min_dist and dist > 0:
                        overlap = min_dist - dist
                        nx = dx / dist
                        ny = dy / dist
                        u1["x"] -= nx * (overlap * 0.5)
                        u1["y"] -= ny * (overlap * 0.5)
                        u2["x"] += nx * (overlap * 0.5)
                        u2["y"] += ny * (overlap * 0.5)

            # Movement & attack execution
            for u in self.units:
                u["is_hit"] = False

                h = get_height_at_pos(u["x"], u["y"], self.heightmap, self.board_size)
                if h <= self.water_level:
                    u["hp"] -= 0.8

                dx = u["target_x"] - u["x"]
                dy = u["target_y"] - u["y"]
                dist = math.hypot(dx, dy)
                if dist > 3:
                    u["is_moving"] = True
                    speed = 2.5
                    u["x"] += (dx / dist) * speed
                    u["y"] += (dy / dist) * speed

                    # Clamp to map boundaries using their collision radius
                    u["x"] = max(u["radius"], min(800 - u["radius"], u["x"]))
                    u["y"] = max(u["radius"], min(800 - u["radius"], u["y"]))
                else:
                    u["is_moving"] = False

                target = next((e for e in self.units if e["id"] == u["target_unit"] and e["owner"] != u["owner"]), None)
                if not target:
                    enemies = [e for e in self.units if e["owner"] != u["owner"]]
                    if enemies:
                        enemies.sort(key=lambda e: math.hypot(e["x"] - u["x"], e["y"] - u["y"]))
                        target = enemies[0]

                if target:
                    e_dist = math.hypot(target["x"] - u["x"], target["y"] - u["y"])
                    can_see = not self.fog_enabled or has_plain_circle_vision(u, target["x"], target["y"])

                    if u["type"] == "Knight":
                        if e_dist < 220 and can_see and now - u["last_attack"] > 1.2:
                            u["last_attack"] = now
                            base_ang = math.atan2(target["y"] - u["y"], target["x"] - u["x"])

                            # Added spread angle for lower projectile accuracy
                            spread = random.uniform(-0.25, 0.25)
                            shot_ang = base_ang + spread
                            u["angle"] = base_ang

                            self.projectiles.append({
                                "x": u["x"], "y": u["y"], "angle": shot_ang,
                                "owner": u["owner"], "damage": 25, "life": 40
                            })
                            self.broadcast({"type": "ATTACK_SOUND", "unit_type": "Knight"})
                    else:
                        if e_dist < (u["radius"] + target["radius"] + 6) and can_see and now - u["last_attack"] > 0.8:
                            u["last_attack"] = now
                            target["hp"] -= 20
                            target["is_hit"] = True
                            self.broadcast({"type": "ATTACK_SOUND", "unit_type": u["type"]})

            alive_projectiles = []
            for p in self.projectiles:
                p["x"] += math.cos(p["angle"]) * 7.0
                p["y"] += math.sin(p["angle"]) * 7.0
                p["life"] -= 1
                hit = False

                for u in self.units:
                    if u["owner"] != p["owner"]:
                        if math.hypot(u["x"] - p["x"], u["y"] - p["y"]) < (u["radius"] + 2):
                            u["hp"] -= p["damage"]
                            u["is_hit"] = True
                            hit = True
                            break

                if not hit and p["life"] > 0:
                    alive_projectiles.append(p)

            self.projectiles = alive_projectiles
            self.units = [u for u in self.units if u["hp"] > 0]

            p0_units = any(u["owner"] == 0 for u in self.units)
            p1_units = any(u["owner"] == 1 for u in self.units)

            if not p0_units or not p1_units:
                winner = 1 if p1_units else 0
                self.scores[winner] += 1
                self.state = "LOBBY"
                self.broadcast({"type": "GAME_OVER", "winner": winner, "scores": self.scores})
            else:
                self.broadcast({
                    "type": "GAME_UPDATE",
                    "units": self.units,
                    "projectiles": self.projectiles,
                    "water_level": self.water_level
                })

    def run(self):
        threading.Thread(target=self.game_loop, daemon=True).start()
        print(f"Server started on {HOST}:{PORT}")
        current_id = 0
        while True:
            conn, addr = self.sock.accept()
            with self.lock:
                self.clients[current_id] = conn
            threading.Thread(target=self.handle_client, args=(current_id, conn), daemon=True).start()
            current_id = (current_id + 1) % 2

if __name__ == "__main__":
    Server().run()

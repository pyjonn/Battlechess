import socket
import threading
import json
import math
import random
import time
import os

HOST = "0.0.0.0"
PORT = 5555

def generate_heightmap(size, water_enabled):
    grid = [[0.0 for _ in range(size)] for _ in range(size)]
    cx, cy = size / 2.0, size / 2.0
    for r in range(size):
        for c in range(size):
            nx, ny = c / float(size), r / float(size)
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

# Replace the old has_plain_circle_vision function with this directional cone check
def has_cone_vision(viewer, target_x, target_y):
    max_range = 200.0
    dx = target_x - viewer["x"]
    dy = target_y - viewer["y"]
    dist = math.hypot(dx, dy)

    if dist > max_range:
        return False

    # Calculate angle to target and compare with viewer's facing angle
    target_angle = math.atan2(dy, dx)
    angle_diff = (target_angle - viewer["angle"] + math.pi) % (2 * math.pi) - math.pi

    # 90-degree total field of view (45 degrees to the left and right)
    return abs(angle_diff) <= math.pi / 4

def lerp_angle(current, target, max_delta):
    diff = (target - current + math.pi) % (2 * math.pi) - math.pi
    if diff > max_delta:
        return current + max_delta
    elif diff < -max_delta:
        return current - max_delta
    return target

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
        self.water_enabled = True
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

            for p in [0, 1]:
                king_x = 400
                king_y = 120 if p == 0 else 680
                self.units.append({
                    "id": self.next_unit_id,
                    "owner": p,
                    "type": "King",
                    "shape": "octagon",
                    "x": king_x,
                    "y": king_y,
                    "target_x": king_x,
                    "target_y": king_y,
                    "hp": 300,
                    "max_hp": 300,
                    "angle": 0.0 if p == 0 else math.pi,
                    "is_moving": False,
                    "is_hit": False,
                    "last_attack": 0,
                    "target_unit": None,
                    "draw_radius": 10,
                    "radius": 13,
                    "vx": 0.0,
                    "vy": 0.0
                })
                self.next_unit_id += 1

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
            costs = {"Pawn": 100, "Knight": 150, "Bishop": 140, "Healer": 180, "Rook": 250, "Queen": 400}
            utype = msg["unit_type"]
            cost = costs.get(utype, 100)

            if self.gold[pid] >= cost:
                self.gold[pid] -= cost

                player_units = [u for u in self.units if u["owner"] == pid]
                row_idx = len(player_units) // 6
                col_idx = len(player_units) % 6

                base_y = 160 if pid == 0 else 640
                spawn_x = 200 + (col_idx * 45)
                spawn_y = base_y + (row_idx * 25 * (1 if pid == 0 else -1))

                shapes = {
                    "Pawn": "circle",
                    "Rook": "square",
                    "Knight": "pentagon",
                    "Queen": "hexagon",
                    "Bishop": "triangle",
                    "Healer": "cross"
                }
                max_hps = {"Pawn": 100, "Knight": 80, "Bishop": 75, "Healer": 90, "Rook": 200, "Queen": 220}
                draw_radii = {"Pawn": 9, "Knight": 11, "Bishop": 10, "Healer": 10, "Rook": 10, "Queen": 11}
                collision_radii = {
                    "Pawn": 15, "Knight": 18, "Bishop": 14, "Healer": 14, "Rook": 16, "Queen": 16
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
                    "angle": 0.0 if pid == 0 else math.pi,
                    "is_moving": False,
                    "is_hit": False,
                    "last_attack": 0,
                    "target_unit": None,
                    "draw_radius": draw_radii[utype],
                    "radius": collision_radii[utype],
                    "vx": 0.0,
                    "vy": 0.0
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

            selected_group = [u for u in self.units if u["owner"] == pid and u["id"] in u_ids]

            if selected_group:
                if t_unit is not None:
                    target_obj = next((e for e in self.units if e["id"] == t_unit), None)
                    for u in selected_group:
                        u["target_unit"] = t_unit
                        if target_obj:
                            u["target_x"] = target_obj["x"]
                            u["target_y"] = target_obj["y"]
                else:
                    center_x = sum(u["x"] for u in selected_group) / len(selected_group)
                    center_y = sum(u["y"] for u in selected_group) / len(selected_group)

                    for u in selected_group:
                        offset_x = u["x"] - center_x
                        offset_y = u["y"] - center_y

                        u["target_x"] = tx + offset_x
                        u["target_y"] = ty + offset_y
                        u["target_unit"] = None

    def game_loop(self):
        has_had_clients = False
        while True:
            time.sleep(0.033)

            with self.lock:
                if len(self.clients) > 0:
                    has_had_clients = True

                if has_had_clients and self.state != "LOBBY" and len(self.clients) == 0:
                    print("All clients disconnected. Shutting down server.")
                    os._exit(0)

            if self.state != "IN_GAME":
                continue

            if self.water_enabled and self.water_rising_enabled:
                self.water_level += 0.001

            now = time.time()

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

            for u in self.units:
                u["is_hit"] = False

                h = get_height_at_pos(u["x"], u["y"], self.heightmap, self.board_size)
                if h <= self.water_level:
                    u["hp"] -= 0.8

                target = None
                if u["type"] == "Healer":
                    if u.get("target_unit"):
                        target = next((e for e in self.units if e["id"] == u["target_unit"] and e["owner"] == u["owner"]), None)
                        if not target:
                            u["target_unit"] = None
                            u["target_x"] = u["x"]
                            u["target_y"] = u["y"]
                    if not target and not u.get("target_unit"):
                        friendlies = [e for e in self.units if e["owner"] == u["owner"] and e["hp"] < e["max_hp"] and e["id"] != u["id"]]
                        if friendlies:
                            friendlies.sort(key=lambda e: math.hypot(e["x"] - u["x"], e["y"] - u["y"]))
                            target = friendlies[0]
                else:
                    if u.get("target_unit"):
                        target = next((e for e in self.units if e["id"] == u["target_unit"] and e["owner"] != u["owner"]), None)
                        if not target:
                            u["target_unit"] = None
                            u["target_x"] = u["x"]
                            u["target_y"] = u["y"]
                    if not target and not u.get("target_unit"):
                        enemies = [e for e in self.units if e["owner"] != u["owner"]]
                        if enemies:
                            enemies.sort(key=lambda e: math.hypot(e["x"] - u["x"], e["y"] - u["y"]))
                            target = enemies[0]

                if target and u.get("target_unit"):
                    u["target_x"] = target["x"]
                    u["target_y"] = target["y"]

                dx = u["target_x"] - u["x"]
                dy = u["target_y"] - u["y"]
                dist = math.hypot(dx, dy)

                if dist > 3:
                    u["is_moving"] = True
                    if u["type"] == "Bishop":
                        speed = 3.6
                    elif u["type"] == "King":
                        speed = 2.0
                    else:
                        speed = 2.5

                    step_x = (dx / dist) * speed
                    step_y = (dy / dist) * speed

                    u["x"] += step_x
                    u["y"] += step_y
                    u["vx"] = step_x
                    u["vy"] = step_y

                    desired_angle = math.atan2(dy, dx)
                    u["angle"] = lerp_angle(u["angle"], desired_angle, 0.15)

                    u["x"] = max(u["radius"], min(800 - u["radius"], u["x"]))
                    u["y"] = max(u["radius"], min(800 - u["radius"], u["y"]))
                else:
                    u["is_moving"] = False
                    u["vx"] = 0.0
                    u["vy"] = 0.0

                if target:
                    desired_angle = math.atan2(target["y"] - u["y"], target["x"] - u["x"])
                    u["angle"] = lerp_angle(u["angle"], desired_angle, 0.15)
                    if target:
                                        e_dist = math.hypot(target["x"] - u["x"], target["y"] - u["y"])
                                        can_see = not self.fog_enabled or has_cone_vision(u, target["x"], target["y"])

                                        # Calculate height difference for bonus damage
                                        attacker_h = get_height_at_pos(u["x"], u["y"], self.heightmap, self.board_size)
                                        target_h = get_height_at_pos(target["x"], target["y"], self.heightmap, self.board_size)
                                        bonus_dmg = int(max(0, attacker_h - target_h) * 15)

                                        if u["type"] == "Knight":
                                            if e_dist < 220 and can_see and now - u["last_attack"] > 1.2:
                                                u["last_attack"] = now
                                                base_ang = math.atan2(target["y"] - u["y"], target["x"] - u["x"])
                                                u["angle"] = base_ang

                                                self.projectiles.append({
                                                    "x": u["x"], "y": u["y"], "angle": base_ang + random.uniform(-0.25, 0.25),
                                                    "owner": u["owner"], "damage": 25 + bonus_dmg, "life": 40
                                                })
                                                self.broadcast({"type": "ATTACK_SOUND", "unit_type": "Knight"})
                                        elif u["type"] == "Healer":
                                            heal_range = (u["radius"] + target["radius"] + 10)
                                            if e_dist < heal_range and now - u["last_attack"] > 0.8:
                                                u["last_attack"] = now
                                                target["hp"] = min(target["max_hp"], target["hp"] + 15)
                                                target["is_hit"] = True
                                                self.broadcast({"type": "ATTACK_SOUND", "unit_type": "Healer"})
                                        else:
                                            attack_range = (u["radius"] + target["radius"] + 8)
                                            cooldown = 0.6 if u["type"] == "Bishop" else (1.0 if u["type"] == "King" else 0.8)
                                            damage_val = 18 if u["type"] == "Bishop" else (30 if u["type"] == "King" else 20)

                                            if e_dist < attack_range and can_see and now - u["last_attack"] > cooldown:
                                                u["last_attack"] = now
                                                target["hp"] -= (damage_val + bonus_dmg)
                                                target["is_hit"] = True
                                                self.broadcast({"type": "ATTACK_SOUND", "unit_type": u["type"]})
                    elif u["type"] == "Healer":
                        heal_range = (u["radius"] + target["radius"] + 10)
                        if e_dist < heal_range and now - u["last_attack"] > 0.8:
                            u["last_attack"] = now
                            target["hp"] = min(target["max_hp"], target["hp"] + 15)
                            target["is_hit"] = True
                            self.broadcast({"type": "ATTACK_SOUND", "unit_type": "Healer"})
                    else:
                        attack_range = (u["radius"] + target["radius"] + 8)
                        if u["type"] == "Bishop":
                            cooldown = 0.6
                            damage_val = 18
                        elif u["type"] == "King":
                            cooldown = 1.0
                            damage_val = 30
                        else:
                            cooldown = 0.8
                            damage_val = 20

                        if e_dist < attack_range and can_see and now - u["last_attack"] > cooldown:
                            u["last_attack"] = now
                            target["hp"] -= damage_val
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

            p0_king = next((u for u in self.units if u["owner"] == 0 and u["type"] == "King"), None)
            p1_king = next((u for u in self.units if u["owner"] == 1 and u["type"] == "King"), None)

            p0_defeated = p0_king is None
            p1_defeated = p1_king is None

            if p0_defeated or p1_defeated:
                winner = 1 if p0_defeated and not p1_defeated else 0
                if p0_defeated and p1_defeated:
                    winner = 1

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

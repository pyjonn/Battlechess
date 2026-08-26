import pygame
import socket
import threading
import json
import math
import sys
import os
import time
import random

pygame.init()
pygame.mixer.init(frequency=44100, size=-16, channels=2)

def load_sound(filename):
    if os.path.exists(filename):
        return pygame.mixer.Sound(filename)
    return pygame.mixer.Sound(buffer=b'\x00' * 44100)

BOW_SOUND = load_sound("bow.wav")
MELEE_SOUND = load_sound("melee.wav")
FOOTSTEP_SOUNDS = [load_sound("step1.wav"), load_sound("step2.wav")]

MELEE_PITCHES = {
    "Pawn": 1.2, "Bishop": 1.4, "Queen": 1.0, "King": 0.8, "Rook": 0.6
}

def play_pitched_melee(unit_type):
    vol = MELEE_PITCHES.get(unit_type, 1.0) * 0.4
    MELEE_SOUND.set_volume(min(1.0, vol))
    MELEE_SOUND.play()

WIDTH, HEIGHT = 800, 600
SCREEN = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("Realtime-Chess")
FONT = pygame.font.SysFont("Arial", 14, bold=True)
BIG_FONT = pygame.font.SysFont("Arial", 20, bold=True)

PLAYER_COLORS = {0: (60, 120, 240), 1: (220, 60, 60)}
SHOP_ITEMS = [("Pawn", 100), ("Knight", 150), ("Bishop", 180), ("Rook", 250), ("Queen", 400)]

BOARD_OFFSET_X = 150
BOARD_OFFSET_Y = 40
BOARD_DIM = 500

class Client:
    def __init__(self, host_ip):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((host_ip, 5555))

        self.player_id = None
        self.scores = {0: 0, 1: 0}
        self.board_size = 24
        self.heightmap = []
        self.water_level = -0.5
        self.starting_gold = 2000
        self.state = "LOBBY"
        self.units = []
        self.projectiles = []
        self.gold = 2000
        self.ready_map = {0: False, 1: False}

        self.chat_messages = []
        self.chat_input = ""
        self.known_players = set()
        self.explored_tiles = set()
        self.visibility_cache = {}
        self.last_vis_check = 0

        self.selected_units = set()
        self.drag_start = None
        self.anim_tick = 0
        self.footstep_index = 0
        self.last_bow_time = 0

        self.fog_enabled = False
        self.water_enabled = False

        threading.Thread(target=self.network_thread, daemon=True).start()

    def network_thread(self):
        buffer = ""
        while True:
            try:
                data = self.sock.recv(4096).decode('utf-8')
                if not data:
                    break
                buffer += data
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    msg = json.loads(line)
                    self.handle_server_msg(msg)
            except:
                break

    def track_players(self, scores_dict):
        for p_str in scores_dict.keys():
            p_id = int(p_str)
            if p_id not in self.known_players:
                self.known_players.add(p_id)
                if self.player_id is not None and p_id != self.player_id:
                    self.chat_messages.append(f"System: Player {p_id + 1} connected!")

    def handle_server_msg(self, msg):
        mtype = msg.get("type")
        if mtype == "INIT":
            self.player_id = msg["player_id"]
            self.scores = {int(k): v for k, v in msg["scores"].items()}
            self.track_players(self.scores)
            self.board_size = msg["board_size"]
            self.heightmap = msg.get("heightmap", [])
            self.water_level = msg.get("water_level", -0.5)
            self.starting_gold = msg.get("starting_gold", 2000)
        elif mtype == "CHAT":
            self.chat_messages.append(f"{msg['sender']}: {msg['text']}")
        elif mtype == "BOARD_SIZE":
            self.board_size = msg["size"]
            self.heightmap = msg.get("heightmap", [])
        elif mtype == "GOLD_SETTING_UPDATE":
            self.starting_gold = msg["starting_gold"]
        elif mtype == "SETTINGS_UPDATE":
            self.fog_enabled = msg.get("fog", self.fog_enabled)
            self.water_enabled = msg.get("water", self.water_enabled)
            if "heightmap" in msg:
                self.heightmap = msg["heightmap"]
        elif mtype == "SHOP_START":
            self.state = "SHOP"
            self.board_size = msg["board_size"]
            self.heightmap = msg.get("heightmap", [])
            self.water_level = msg.get("water_level", -0.5)
            self.units = msg["units"]
            self.gold = msg["gold"].get(str(self.player_id), msg["gold"].get(self.player_id, self.starting_gold))
            self.explored_tiles.clear()
            self.visibility_cache.clear()
        elif mtype == "SHOP_UPDATE":
            self.units = msg["units"]
            g = msg["gold"]
            self.gold = g.get(str(self.player_id), g.get(self.player_id, self.starting_gold))
            rm = msg["ready"]
            self.ready_map = {int(k): v for k, v in rm.items()}
            self.track_players(rm)
        elif mtype == "GAME_START":
            self.units = msg["units"]
            self.projectiles = []
            self.board_size = msg["board_size"]
            self.heightmap = msg.get("heightmap", [])
            self.water_level = msg.get("water_level", -0.5)
            self.state = "IN_GAME"
            self.selected_units.clear()
            self.explored_tiles.clear()
            self.visibility_cache.clear()
        elif mtype == "ATTACK_SOUND":
            utype = msg.get("unit_type")
            if utype == "Knight":
                now = time.time()
                if now - self.last_bow_time > 0.05:
                    BOW_SOUND.set_volume(random.uniform(0.2, 0.4))
                    BOW_SOUND.play()
                    self.last_bow_time = now
            else:
                play_pitched_melee(utype)
        elif mtype == "GAME_UPDATE":
            self.units = msg["units"]
            self.projectiles = msg.get("projectiles", [])
            self.water_level = msg.get("water_level", self.water_level)
        elif mtype == "GAME_OVER":
            self.scores = {int(k): v for k, v in msg["scores"].items()}
            self.state = "LOBBY"
            self.chat_messages.append(f"System: Player {msg['winner']+1} won the round!")

    def send(self, data):
        self.sock.sendall((json.dumps(data) + "\n").encode('utf-8'))

    def to_screen_coords(self, x, y):
        cx = BOARD_OFFSET_X + (x / 800.0) * BOARD_DIM
        cy = BOARD_OFFSET_Y + (y / 800.0) * BOARD_DIM
        if self.player_id == 0:
            return (BOARD_OFFSET_X + BOARD_DIM) - (cx - BOARD_OFFSET_X), (BOARD_OFFSET_Y + BOARD_DIM) - (cy - BOARD_OFFSET_Y)
        return cx, cy

    def to_world_coords(self, sx, sy):
        if self.player_id == 0:
            sx = (BOARD_OFFSET_X + BOARD_DIM) - (sx - BOARD_OFFSET_X)
            sy = (BOARD_OFFSET_Y + BOARD_DIM) - (sy - BOARD_OFFSET_Y)
        wx = ((sx - BOARD_OFFSET_X) / BOARD_DIM) * 800.0
        wy = ((sy - BOARD_OFFSET_Y) / BOARD_DIM) * 800.0
        return wx, wy

    def to_screen_angle(self, angle):
        if self.player_id == 0:
            return angle + math.pi
        return angle

    def update_footsteps(self):
        moving_count = sum(1 for u in self.units if u.get("is_moving", False) and u["owner"] == self.player_id)
        if moving_count > 0:
            rate = max(4, 15 - (moving_count // 2))
            if self.anim_tick % rate == 0:
                snd = FOOTSTEP_SOUNDS[self.footstep_index % len(FOOTSTEP_SOUNDS)]
                volume = min(0.8, 0.1 + (moving_count * 0.08))
                snd.set_volume(volume)
                snd.play()
                self.footstep_index += 1

    def is_point_currently_visible(self, wx, wy):
        if not self.fog_enabled:
            return True

        q_key = (int(wx // 10), int(wy // 10))
        now = time.time()

        if now - self.last_vis_check < 0.10 and q_key in self.visibility_cache:
            return self.visibility_cache[q_key]

        visible = False
        vision_radius = 200.0
        for u in self.units:
            if u["owner"] == self.player_id:
                if math.hypot(wx - u["x"], wy - u["y"]) <= vision_radius:
                    visible = True
                    break

        self.visibility_cache[q_key] = visible
        return visible

    def run(self):
        clock = pygame.time.Clock()
        running = True

        while running:
            clock.tick(60)
            self.anim_tick += 1
            if time.time() - self.last_vis_check >= 0.10:
                self.visibility_cache.clear()
                self.last_vis_check = time.time()

            if self.state == "IN_GAME":
                self.update_footsteps()

            events = pygame.event.get()
            for event in events:
                if event.type == pygame.QUIT:
                    running = False

                if self.state == "LOBBY":
                    self.handle_lobby_events(event)
                elif self.state == "SHOP":
                    self.handle_shop_events(event)
                elif self.state == "IN_GAME":
                    self.handle_game_events(event)

            SCREEN.fill((30, 30, 35))
            if self.state == "LOBBY":
                self.draw_lobby()
            elif self.state == "SHOP":
                self.draw_shop()
            elif self.state == "IN_GAME":
                self.draw_game()

            pygame.display.flip()

        pygame.quit()
        sys.exit()

    def handle_lobby_events(self, event):
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_RETURN and self.chat_input.strip():
                self.send({"type": "CHAT", "text": self.chat_input})
                self.chat_input = ""
            elif event.key == pygame.K_BACKSPACE:
                self.chat_input = self.chat_input[:-1]
            elif event.key == pygame.K_UP and self.player_id == 0:
                self.send({"type": "SET_BOARD_SIZE", "size": min(48, self.board_size + 2)})
            elif event.key == pygame.K_DOWN and self.player_id == 0:
                self.send({"type": "SET_BOARD_SIZE", "size": max(12, self.board_size - 2)})
            elif event.key == pygame.K_EQUALS and self.player_id == 0:
                self.send({"type": "SET_STARTING_GOLD", "gold": self.starting_gold + 250})
            elif event.key == pygame.K_MINUS and self.player_id == 0:
                self.send({"type": "SET_STARTING_GOLD", "gold": self.starting_gold - 250})
            elif event.key == pygame.K_f and self.player_id == 0:
                self.fog_enabled = not self.fog_enabled
                self.send({"type": "TOGGLE_FOG", "fog": self.fog_enabled})
            elif event.key == pygame.K_w and self.player_id == 0:
                self.water_enabled = not self.water_enabled
                self.send({"type": "TOGGLE_WATER", "water": self.water_enabled})
            elif event.key == pygame.K_SPACE and self.player_id == 0:
                self.send({"type": "START_GAME"})
            else:
                if len(self.chat_input) < 40 and event.unicode.isprintable():
                    self.chat_input += event.unicode

    def handle_shop_events(self, event):
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            for i, (name, cost) in enumerate(SHOP_ITEMS):
                btn_rect = pygame.Rect(10 + i * 125, 550, 118, 38)
                if btn_rect.collidepoint(mx, my):
                    self.send({"type": "BUY_UNIT", "unit_type": name})
                    break

            ready_rect = pygame.Rect(645, 550, 145, 38)
            if ready_rect.collidepoint(mx, my):
                self.send({"type": "READY_SHOP"})

    def handle_game_events(self, event):
        if event.type == pygame.MOUSEBUTTONDOWN:
            smx, smy = event.pos
            wmx, wmy = self.to_world_coords(smx, smy)

            if event.button == 1:
                self.drag_start = (smx, smy)
                if not (pygame.key.get_mods() & pygame.KMOD_SHIFT):
                    self.selected_units.clear()

                for u in self.units:
                    r = u.get("radius", 14)
                    if u["owner"] == self.player_id and math.hypot(u["x"] - wmx, u["y"] - wmy) < (r + 5):
                        self.selected_units.add(u["id"])

            elif event.button == 3:
                if self.selected_units:
                    target_u = None
                    for u in self.units:
                        r = u.get("radius", 14)
                        if u["owner"] != self.player_id and math.hypot(u["x"] - wmx, u["y"] - wmy) < (r + 5):
                            target_u = u["id"]
                            break
                    self.send({
                        "type": "COMMAND",
                        "unit_ids": list(self.selected_units),
                        "target_pos": [wmx, wmy],
                        "target_unit": target_u
                    })

        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1 and self.drag_start:
            smx, smy = event.pos
            sx1, sx2 = min(self.drag_start[0], smx), max(self.drag_start[0], smx)
            sy1, sy2 = min(self.drag_start[1], smy), max(self.drag_start[1], smy)

            if abs(sx2 - sx1) > 5 or abs(sy2 - sy1) > 5:
                for u in self.units:
                    if u["owner"] == self.player_id:
                        ux, uy = self.to_screen_coords(u["x"], u["y"])
                        if sx1 <= ux <= sx2 and sy1 <= uy <= sy2:
                            self.selected_units.add(u["id"])
            self.drag_start = None

    def draw_lobby(self):
        title = BIG_FONT.render("Realtime-Chess Waiting Room", True, (255, 255, 255))
        SCREEN.blit(title, (40, 15))
        score_txt = FONT.render(f"Score - P1: {self.scores.get(0, 0)} | P2: {self.scores.get(1, 0)}", True, (200, 200, 200))
        SCREEN.blit(score_txt, (40, 40))

        host_opts1 = f"Grid: {self.board_size}x{self.board_size} (Up/Down) | Start Gold: ${self.starting_gold} (+/-)"
        host_opts2 = f"Fog: {'ON' if self.fog_enabled else 'OFF'} (F) | Water Mode: {'RISING' if self.water_enabled else 'STATIC'} (W)"
        SCREEN.blit(FONT.render(host_opts1, True, (200, 200, 200)), (40, 60))
        SCREEN.blit(FONT.render(host_opts2, True, (200, 200, 200)), (40, 78))

        start_txt = FONT.render("Press SPACE to Start (Host Only)", True, (100, 255, 100))
        SCREEN.blit(start_txt, (40, 96))

        pygame.draw.rect(SCREEN, (20, 20, 25), (40, 120, 720, 410))
        for i, msg in enumerate(self.chat_messages[-16:]):
            txt = FONT.render(msg, True, (220, 220, 220))
            SCREEN.blit(txt, (50, 130 + i * 22))

        pygame.draw.rect(SCREEN, (40, 40, 50), (40, 545, 720, 35))
        in_txt = FONT.render(f"Chat: {self.chat_input}", True, (255, 255, 255))
        SCREEN.blit(in_txt, (50, 553))

    def draw_board(self):
        tile_size = BOARD_DIM / float(self.board_size)
        for r in range(self.board_size):
            for c in range(self.board_size):
                map_r = self.board_size - 1 - r if self.player_id == 0 else r
                map_c = self.board_size - 1 - c if self.player_id == 0 else c

                cell_wx = (map_c + 0.5) * (800.0 / self.board_size)
                cell_wy = (map_r + 0.5) * (800.0 / self.board_size)

                currently_visible = self.is_point_currently_visible(cell_wx, cell_wy)

                if currently_visible:
                    self.explored_tiles.add((map_r, map_c))

                is_explored = (map_r, map_c) in self.explored_tiles

                if not is_explored and self.fog_enabled:
                    color = (0, 0, 0)
                else:
                    base_color = (235, 235, 208) if (map_r + map_c) % 2 == 0 else (119, 148, 85)

                    if self.heightmap and map_r < len(self.heightmap) and map_c < len(self.heightmap[map_r]):
                        h = self.heightmap[map_r][map_c]

                        if h <= self.water_level:
                            depth = min(1.0, (self.water_level - h) * 0.6)
                            color = (
                                int(40 * (1 - depth)),
                                int(120 * (1 - depth) + 20),
                                int(220 * (1 - depth) + 30)
                            )
                        elif h > 0:
                            r_c = min(255, int(base_color[0] + h * 70))
                            g_c = min(255, int(base_color[1] + h * 40))
                            b_c = max(0, int(base_color[2] - h * 50))
                            color = (r_c, g_c, b_c)
                        else:
                            factor = max(0.7, 1.0 + (h * 0.2))
                            color = (int(base_color[0] * factor), int(base_color[1] * factor), int(base_color[2] * factor))
                    else:
                        color = base_color

                    if self.fog_enabled and not currently_visible:
                        color = (int(color[0] * 0.4), int(color[1] * 0.4), int(color[2] * 0.4))

                x1 = math.floor(BOARD_OFFSET_X + c * tile_size)
                y1 = math.floor(BOARD_OFFSET_Y + r * tile_size)
                x2 = math.floor(BOARD_OFFSET_X + (c + 1) * tile_size)
                y2 = math.floor(BOARD_OFFSET_Y + (r + 1) * tile_size)

                pygame.draw.rect(SCREEN, color, (x1, y1, x2 - x1, y2 - y1))

    def draw_feet(self, sx, sy, s_angle, is_moving, radius):
        foot_phase = math.sin(self.anim_tick * 0.4) * 3 if is_moving else 0
        perp_angle = s_angle + math.pi / 2
        offset = radius * 0.5

        lx = sx + offset * math.cos(perp_angle) + foot_phase * math.cos(s_angle)
        ly = sy + offset * math.sin(perp_angle) + foot_phase * math.sin(s_angle)

        rx = sx - offset * math.cos(perp_angle) - foot_phase * math.cos(s_angle)
        ry = sy - offset * math.sin(perp_angle) - foot_phase * math.sin(s_angle)

        pygame.draw.circle(SCREEN, (50, 50, 50), (int(lx), int(ly)), 2)
        pygame.draw.circle(SCREEN, (50, 50, 50), (int(rx), int(ry)), 2)

    def draw_units_and_projectiles(self):
        for p in self.projectiles:
            if not self.is_point_currently_visible(p["x"], p["y"]):
                continue
            sx, sy = self.to_screen_coords(p["x"], p["y"])
            ang = self.to_screen_angle(p["angle"])
            end_x = sx + int(10 * math.cos(ang))
            end_y = sy + int(10 * math.sin(ang))
            pygame.draw.line(SCREEN, (255, 220, 0), (sx, sy), (end_x, end_y), 2)

        for u in self.units:
            if u["owner"] != self.player_id and not self.is_point_currently_visible(u["x"], u["y"]):
                continue

            sx, sy = self.to_screen_coords(u["x"], u["y"])
            s_angle = self.to_screen_angle(u["angle"])
            color = PLAYER_COLORS[u["owner"]]

            is_hit = u.get("is_hit", False)
            is_moving = u.get("is_moving", False)
            draw_radius = u.get("draw_radius", 10)
            collision_radius = u.get("radius", 14)

            self.draw_feet(sx, sy, s_angle, is_moving, draw_radius)

            if u["id"] in self.selected_units:
                pygame.draw.circle(SCREEN, (255, 255, 0), (int(sx), int(sy)), collision_radius, 1)

            shape = u["shape"]
            draw_color = (255, 255, 255) if is_hit else color

            if shape == "circle":
                pygame.draw.circle(SCREEN, draw_color, (int(sx), int(sy)), draw_radius)
            elif shape == "square":
                pygame.draw.rect(SCREEN, draw_color, (int(sx) - draw_radius, int(sy) - draw_radius, draw_radius * 2, draw_radius * 2))
            else:
                sides = 5 if shape == "pentagon" else 6 if shape == "hexagon" else 8
                points = []
                for i in range(sides):
                    a = s_angle + i * (2 * math.pi / sides)
                    points.append((sx + draw_radius * math.cos(a), sy + draw_radius * math.sin(a)))
                pygame.draw.polygon(SCREEN, draw_color, points)

            end_x = sx + (draw_radius + 4) * math.cos(s_angle)
            end_y = sy + (draw_radius + 4) * math.sin(s_angle)
            pygame.draw.line(SCREEN, (255, 255, 255), (sx, sy), (end_x, end_y), 2)

            hp_ratio = max(0, u["hp"] / u["max_hp"])
            pygame.draw.rect(SCREEN, (255, 0, 0), (int(sx) - draw_radius, int(sy) - draw_radius - 5, draw_radius * 2, 3))
            pygame.draw.rect(SCREEN, (0, 255, 0), (int(sx) - draw_radius, int(sy) - draw_radius - 5, int((draw_radius * 2) * hp_ratio), 3))

    def draw_shop(self):
        self.draw_board()
        self.draw_units_and_projectiles()

        hdr = pygame.Surface((WIDTH, BOARD_OFFSET_Y))
        hdr.fill((15, 15, 20))
        SCREEN.blit(hdr, (0, 0))
        title = BIG_FONT.render(f"Buy Phase - Gold: ${self.gold}", True, (255, 215, 0))
        SCREEN.blit(title, (15, 8))

        pygame.draw.rect(SCREEN, (20, 20, 25), (0, 540, 800, 60))
        for i, (name, cost) in enumerate(SHOP_ITEMS):
            rect = pygame.Rect(10 + i * 125, 550, 118, 38)
            pygame.draw.rect(SCREEN, (50, 50, 75), rect, border_radius=4)
            txt = FONT.render(f"+ {name} (${cost})", True, (255, 255, 255))
            SCREEN.blit(txt, (rect.x + 8, rect.y + 10))

        ready_color = (0, 180, 0) if self.ready_map.get(self.player_id, False) else (190, 40, 40)
        ready_rect = pygame.Rect(645, 550, 145, 38)
        pygame.draw.rect(SCREEN, ready_color, ready_rect, border_radius=4)
        rtxt = BIG_FONT.render("READY", True, (255, 255, 255))
        SCREEN.blit(rtxt, (ready_rect.x + 40, ready_rect.y + 7))

    def draw_game(self):
        header = pygame.Surface((WIDTH, BOARD_OFFSET_Y))
        header.fill((15, 15, 20))
        SCREEN.blit(header, (0, 0))

        score_txt = FONT.render(f"Score - P1: {self.scores.get(0,0)}  vs  P2: {self.scores.get(1,0)} | Playing as Player {self.player_id + 1}", True, (255, 255, 255))
        SCREEN.blit(score_txt, (15, 12))

        self.draw_board()
        self.draw_units_and_projectiles()

        if self.drag_start and pygame.mouse.get_pressed()[0]:
            mx, my = pygame.mouse.get_pos()
            x1, x2 = min(self.drag_start[0], mx), max(self.drag_start[0], mx)
            y1, y2 = min(self.drag_start[1], my), max(self.drag_start[1], my)
            pygame.draw.rect(SCREEN, (0, 255, 0), (x1, y1, x2 - x1, y2 - y1), 1)

if __name__ == "__main__":
    ip = input("Enter Server IP (or press Enter for localhost): ").strip() or "127.0.0.1"
    uv_client = Client(ip)
    uv_client.run()

# client.py
import pygame
import socket
import threading
import json
import math
import sys
import os
import time
import random
import subprocess

pygame.init()
pygame.mixer.pre_init(44100, -16, 2, 512)
pygame.init()
pygame.mixer.set_num_channels(16)

try:
    pygame.mixer.music.load("soundtrack.ogg")
    pygame.mixer.music.set_volume(0.3)
    pygame.mixer.music.play(-1)
except Exception as e:
    print(f"Could not load soundtrack: {e}")

try:
    BOW_SOUND = pygame.mixer.Sound("bow.wav")
except Exception:
    BOW_SOUND = None

try:
    MELEE_SOUND = pygame.mixer.Sound("melee.wav")
except Exception:
    MELEE_SOUND = None

try:
    FOOTSTEP_SOUNDS = [pygame.mixer.Sound("step1.wav"), pygame.mixer.Sound("step2.wav")]
except Exception:
    FOOTSTEP_SOUNDS = []

def generate_beep(frequency, duration, volume=0.5):
    sample_rate = 44100
    num_samples = int(sample_rate * duration)
    buffer = bytearray()
    for i in range(num_samples):
        t = i / sample_rate
        value = int(127 * math.sin(2 * math.pi * frequency * t) * volume + 128)
        buffer.append(max(0, min(255, value)))
    return pygame.mixer.Sound(buffer=bytes(buffer))

def generate_noise(duration, volume=0.5):
    sample_rate = 44100
    num_samples = int(sample_rate * duration)
    buffer = bytearray()
    for i in range(num_samples):
        val = int(random.randint(0, 255) * volume)
        buffer.append(val)
    return pygame.mixer.Sound(buffer=bytes(buffer))

if BOW_SOUND is None:
    BOW_SOUND = generate_beep(450, 0.1, 0.3)
if MELEE_SOUND is None:
    MELEE_SOUND = generate_beep(120, 0.08, 0.4)
if not FOOTSTEP_SOUNDS:
    FOOTSTEP_SOUNDS = [generate_noise(0.05, 0.15), generate_noise(0.05, 0.15)]

MELEE_PITCHES = {
    "Pawn": 1.2, "Bishop": 1.4, "Queen": 1.0, "King": 0.8, "Rook": 0.6, "Healer": 1.5
}

def play_pitched_melee(unit_type):
    vol = MELEE_PITCHES.get(unit_type, 1.0) * 0.4
    MELEE_SOUND.set_volume(min(1.0, vol))
    MELEE_SOUND.play()

WIDTH, HEIGHT = 800, 600
SCREEN = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("Realtime-Chess Client")
FONT = pygame.font.SysFont("Arial", 14, bold=True)
BIG_FONT = pygame.font.SysFont("Arial", 20, bold=True)

PLAYER_COLORS = {
    0: (60, 120, 240),  # Blue
    1: (220, 60, 60),   # Red
    2: (60, 220, 60),   # Green
    3: (220, 220, 60)   # Yellow
}

SHOP_ITEMS = [
    ("Pawn", 100),
    ("Knight", 150),
    ("Bishop", 140),
    ("Healer", 180),
    ("Rook", 250),
    ("Queen", 400)
]

WORLD_SIZE = 800.0

class ClientApp:
    def __init__(self):
        self.state = "MENU"
        self.server_process = None
        self.sock = None
        self.username = "Player"
        self.active_field = "username"
        self.ip_input = "127.0.0.1"
        self.player_id = None
        self.scores = {}
        self.board_size = 24
        self.heightmap = []
        self.water_level = -0.5
        self.water_rising = False
        self.starting_gold = 2000
        self.game_state = "LOBBY"
        self.game_mode = "FFA"
        self.units = []
        self.projectiles = []
        self.gold = 2000
        self.ready_map = {}
        self.chat_messages = []
        self.chat_input = ""
        self.selected_units = set()
        self.drag_start = None
        self.anim_tick = 0
        self.footstep_index = 0
        self.last_bow_time = 0
        self.fog_enabled = False
        self.water_enabled = False

        self.zoom = 1.0
        self.camera_x = 0.0
        self.camera_y = 0.0
        self.show_minimap = True

    def update_default_zoom(self):
        self.zoom = 1.0
        self.camera_x = 0.0
        self.camera_y = 0.0

    def run(self):
        clock = pygame.time.Clock()
        while True:
            dt = clock.tick(60) / 1000.0
            self.anim_tick += 1

            if self.state == "CONNECTED" and self.game_state in ("SHOP", "IN_GAME"):
                keys = pygame.key.get_pressed()
                pan_speed = 400.0 * dt / self.zoom
                max_cam = max(0.0, WORLD_SIZE - (WORLD_SIZE / self.zoom))

                # Camera operations are inherently bound to the rotated spatial perspective of the active player
                if keys[pygame.K_w] or keys[pygame.K_UP]:
                    self.camera_y = max(0.0, self.camera_y - pan_speed)
                if keys[pygame.K_s] or keys[pygame.K_DOWN]:
                    self.camera_y = min(max_cam, self.camera_y + pan_speed)
                if keys[pygame.K_a] or keys[pygame.K_LEFT]:
                    self.camera_x = max(0.0, self.camera_x - pan_speed)
                if keys[pygame.K_d] or keys[pygame.K_RIGHT]:
                    self.camera_x = min(max_cam, self.camera_x + pan_speed)

            events = pygame.event.get()
            for event in events:
                if event.type == pygame.QUIT:
                    self.cleanup_and_exit()

                if self.state == "MENU":
                    self.handle_menu_events(event)
                elif self.state == "CONNECTED":
                    if self.game_state == "LOBBY":
                        self.handle_lobby_events(event)
                    elif self.game_state == "SHOP":
                        self.handle_shop_events(event)
                    elif self.game_state == "IN_GAME":
                        self.handle_game_events(event)

            SCREEN.fill((30, 30, 35))
            if self.state == "MENU":
                self.draw_menu()
            else:
                if self.game_state == "LOBBY":
                    self.draw_lobby()
                elif self.game_state == "SHOP":
                    self.draw_shop()
                elif self.game_state == "IN_GAME":
                    self.draw_game()
            pygame.display.flip()

    def cleanup_and_exit(self):
        if self.server_process:
            self.server_process.terminate()
            try:
                self.server_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.server_process.kill()
        pygame.quit()
        sys.exit()

    def connect_to_server(self, ip):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((ip, 5555))
            threading.Thread(target=self.network_thread, daemon=True).start()
            self.state = "CONNECTED"
            self.send({"type": "SET_NAME", "username": self.username})
        except Exception as e:
            self.chat_messages.append(f"Connection failed: {e}")

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

        self.state = "MENU"
        self.game_state = "LOBBY"
        if self.sock:
            self.sock.close()
            self.sock = None
        self.chat_messages.append("System: Server closed. Returned to main menu.")

    def handle_server_msg(self, msg):
        mtype = msg.get("type")
        if mtype == "INIT":
            self.player_id = msg["player_id"]
            self.scores = {int(k): v for k, v in msg["scores"].items()}
            self.board_size = msg["board_size"]
            self.heightmap = msg.get("heightmap", [])
            self.water_level = msg.get("water_level", -0.5)
            self.starting_gold = msg["starting_gold"]
            self.game_mode = msg.get("game_mode", "FFA")
            self.update_default_zoom()
        elif mtype == "PLAYER_DISCONNECT":
            self.scores = {int(k): v for k, v in msg["scores"].items()}
            self.game_state = "LOBBY"
            self.state = "CONNECTED"
            self.units = []
            self.projectiles = []
            self.chat_messages.append(f"System: Player {msg['disconnected_id'] + 1} disconnected. Returned to lobby.")
        elif mtype == "CHAT":
            self.chat_messages.append(f"{msg['sender']}: {msg['text']}")
        elif mtype == "BOARD_SIZE":
            self.board_size = msg["size"]
            self.heightmap = msg.get("heightmap", [])
            self.update_default_zoom()
        elif mtype == "GOLD_SETTING_UPDATE":
            self.starting_gold = msg["starting_gold"]
        elif mtype == "SETTINGS_UPDATE":
            self.fog_enabled = msg.get("fog", self.fog_enabled)
            self.water_enabled = msg.get("water", self.water_enabled)
            self.game_mode = msg.get("game_mode", self.game_mode)
            if "water_rising" in msg:
                self.water_rising = msg["water_rising"]
            if "heightmap" in msg:
                self.heightmap = msg["heightmap"]
        elif mtype == "SHOP_START":
            self.game_state = "SHOP"
            self.board_size = msg["board_size"]
            self.heightmap = msg.get("heightmap", [])
            self.water_level = msg.get("water_level", -0.5)
            self.units = msg["units"]
            self.gold = msg["gold"].get(str(self.player_id), msg["gold"].get(self.player_id, self.starting_gold))
            self.update_default_zoom()
        elif mtype == "SHOP_UPDATE":
            self.units = msg["units"]
            g = msg["gold"]
            self.gold = g.get(str(self.player_id), g.get(self.player_id, self.starting_gold))
            self.ready_map = {int(k): v for k, v in msg["ready"].items()}
            if "heightmap" in msg:
                self.heightmap = msg["heightmap"]
        elif mtype == "GAME_START":
            self.units = msg["units"]
            self.projectiles = []
            self.board_size = msg["board_size"]
            self.heightmap = msg.get("heightmap", [])
            self.water_level = msg.get("water_level", -0.5)
            self.game_state = "IN_GAME"
            self.selected_units.clear()
            self.update_default_zoom()
        elif mtype == "ATTACK_SOUND":
            utype = msg.get("unit_type")
            if utype == "Knight":
                now = time.time()
                if now - self.last_bow_time > 0.05:
                    if BOW_SOUND:
                        BOW_SOUND.set_volume(random.uniform(0.2, 0.4))
                        BOW_SOUND.play()
                    self.last_bow_time = now
            else:
                play_pitched_melee(utype)
        elif mtype == "GAME_UPDATE":
            old_positions = {u["id"]: (u["x"], u["y"]) for u in self.units}
            self.units = msg["units"]
            self.projectiles = msg.get("projectiles", [])
            self.water_level = msg.get("water_level", self.water_level)

            moving_count = 0
            for u in self.units:
                if u["owner"] == self.player_id and u["id"] in old_positions:
                    ox, oy = old_positions[u["id"]]
                    if math.hypot(u["x"] - ox, u["y"] - oy) > 0.4:
                        moving_count += 1

            threshold = max(5, 25 - (moving_count * 3))
            if moving_count > 0 and self.anim_tick % threshold == 0 and FOOTSTEP_SOUNDS:
                FOOTSTEP_SOUNDS[self.footstep_index].set_volume(0.1)
                FOOTSTEP_SOUNDS[self.footstep_index].play()
                self.footstep_index = 1 - self.footstep_index
        elif mtype == "GAME_OVER":
            self.scores = {int(k): v for k, v in msg["scores"].items()}
            self.game_state = "LOBBY"
            winner = msg['winner']
            if self.game_mode == "2v2":
                self.chat_messages.append(f"System: Team {winner + 1} won the round!")
            else:
                self.chat_messages.append(f"System: Player {winner + 1} won the round!")

    def send(self, data):
        if self.sock:
            self.sock.sendall((json.dumps(data) + "\n").encode('utf-8'))

    def handle_menu_events(self, event):
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            if pygame.Rect(WIDTH // 2 - 100, 210, 200, 40).collidepoint(mx, my):
                try:
                    self.server_process = subprocess.Popen([sys.executable, "server.py"])
                    time.sleep(0.8)
                    self.connect_to_server("127.0.0.1")
                except Exception as e:
                    self.chat_messages.append(f"Server start failed: {e}")
            elif pygame.Rect(WIDTH // 2 - 100, 265, 200, 35).collidepoint(mx, my):
                self.active_field = "username"
            elif pygame.Rect(WIDTH // 2 - 100, 310, 200, 35).collidepoint(mx, my):
                self.active_field = "ip"
            elif pygame.Rect(WIDTH // 2 - 100, 355, 200, 40).collidepoint(mx, my):
                self.connect_to_server(self.ip_input)
        elif event.type == pygame.KEYDOWN:
            target_str = self.username if self.active_field == "username" else self.ip_input
            if event.key == pygame.K_BACKSPACE:
                target_str = target_str[:-1]
            elif event.key == pygame.K_TAB:
                self.active_field = "ip" if self.active_field == "username" else "username"
            elif event.unicode.isprintable() and len(target_str) < 20:
                target_str += event.unicode
            if self.active_field == "username":
                self.username = target_str
            else:
                self.ip_input = target_str

    def draw_menu(self):
        SCREEN.fill((25, 25, 30))
        mx, my = pygame.mouse.get_pos()
        t = BIG_FONT.render("Realtime Chess - Main Menu", True, (255, 255, 255))
        SCREEN.blit(t, (WIDTH // 2 - t.get_width() // 2, 140))

        host_rect = pygame.Rect(WIDTH // 2 - 100, 210, 200, 40)
        host_col = (70, 180, 70) if host_rect.collidepoint(mx, my) else (50, 150, 50)
        pygame.draw.rect(SCREEN, host_col, host_rect, border_radius=6)
        ht = FONT.render("Host Game Server", True, (255, 255, 255))
        SCREEN.blit(ht, (WIDTH // 2 - ht.get_width() // 2, 222))

        is_user_active = (self.active_field == "username")
        u_rect = pygame.Rect(WIDTH // 2 - 100, 265, 200, 35)
        u_col = (80, 120, 220) if is_user_active else (50, 50, 70)
        pygame.draw.rect(SCREEN, u_col, u_rect, border_radius=6)
        if is_user_active:
            pygame.draw.rect(SCREEN, (255, 255, 255), u_rect, width=2, border_radius=6)
        ut = FONT.render(f"Name: {self.username}", True, (255, 255, 255))
        SCREEN.blit(ut, (WIDTH // 2 - 90, 274))

        is_ip_active = (self.active_field == "ip")
        ip_rect = pygame.Rect(WIDTH // 2 - 100, 310, 200, 35)
        ip_col = (80, 120, 220) if is_ip_active else (50, 50, 70)
        pygame.draw.rect(SCREEN, ip_col, ip_rect, border_radius=6)
        if is_ip_active:
            pygame.draw.rect(SCREEN, (255, 255, 255), ip_rect, width=2, border_radius=6)
        jt = FONT.render(f"IP: {self.ip_input}", True, (255, 255, 255))
        SCREEN.blit(jt, (WIDTH // 2 - 90, 319))

        join_rect = pygame.Rect(WIDTH // 2 - 100, 355, 200, 40)
        join_col = (70, 110, 210) if join_rect.collidepoint(mx, my) else (50, 90, 180)
        pygame.draw.rect(SCREEN, join_col, join_rect, border_radius=6)
        jbt = FONT.render("Join Server", True, (255, 255, 255))
        SCREEN.blit(jbt, (WIDTH // 2 - jbt.get_width() // 2, 366))

    def handle_lobby_events(self, event):
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_RETURN and self.chat_input.strip():
                self.send({"type": "CHAT", "text": self.chat_input})
                self.chat_input = ""
            elif event.key == pygame.K_BACKSPACE:
                self.chat_input = self.chat_input[:-1]
            elif event.key == pygame.K_UP and self.player_id == 0:
                self.send({"type": "SET_BOARD_SIZE", "size": min(128, self.board_size + 2)})
            elif event.key == pygame.K_DOWN and self.player_id == 0:
                self.send({"type": "SET_BOARD_SIZE", "size": max(12, self.board_size - 2)})
            elif event.key == pygame.K_RIGHT and self.player_id == 0:
                self.send({"type": "SET_STARTING_GOLD", "starting_gold": min(5000, self.starting_gold + 100)})
            elif event.key == pygame.K_LEFT and self.player_id == 0:
                self.send({"type": "SET_STARTING_GOLD", "starting_gold": max(100, self.starting_gold - 100)})
            elif event.key == pygame.K_w and self.player_id == 0:
                self.send({"type": "SET_WATER_RISING", "rising": not self.water_rising})
            elif event.key == pygame.K_m and self.player_id == 0:
                self.send({"type": "TOGGLE_MODE"})
            elif event.key == pygame.K_SPACE and self.player_id == 0:
                self.send({"type": "START_GAME"})
            elif event.unicode.isprintable() and len(self.chat_input) < 40:
                self.chat_input += event.unicode

    def handle_shop_events(self, event):
        self.handle_common_board_events(event)
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            for i, (name, cost) in enumerate(SHOP_ITEMS):
                if pygame.Rect(10 + i * 110, 550, 105, 38).collidepoint(mx, my):
                    self.send({"type": "BUY_UNIT", "unit_type": name})
                    break
            if pygame.Rect(680, 550, 110, 38).collidepoint(mx, my):
                self.send({"type": "READY_SHOP"})

    def handle_game_events(self, event):
        self.handle_common_board_events(event)
        if event.type == pygame.MOUSEBUTTONDOWN:
            smx, smy = event.pos
            if smy > 40:
                wmx, wmy = self.to_world_coords(smx, smy)
                if event.button == 1:
                    self.drag_start = (smx, smy)
                    if not (pygame.key.get_mods() & pygame.KMOD_SHIFT):
                        self.selected_units.clear()
                    for u in self.units:
                        if u["owner"] == self.player_id:
                            u_blocks = 2.4 if u["type"] in ("Queen", "Rook") else 2.0
                            u_radius_world = (u_blocks / self.board_size) * WORLD_SIZE * 0.5
                            if math.hypot(u["x"] - wmx, u["y"] - wmy) < (u_radius_world + 8 / self.zoom):
                                self.selected_units.add(u["id"])
                elif event.button == 3:
                    if self.selected_units:
                        target_u = None
                        for u in self.units:
                            if u["owner"] != self.player_id:
                                u_blocks = 2.4 if u["type"] in ("Queen", "Rook") else 2.0
                                u_radius_world = (u_blocks / self.board_size) * WORLD_SIZE * 0.5
                                if math.hypot(u["x"] - wmx, u["y"] - wmy) < (u_radius_world + 8 / self.zoom):
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
            if self.drag_start[1] > 40 and smy > 40:
                sx1, sx2 = min(self.drag_start[0], smx), max(self.drag_start[0], smx)
                sy1, sy2 = min(self.drag_start[1], smy), max(self.drag_start[1], smy)
                if abs(sx2 - sx1) > 5 or abs(sy2 - sy1) > 5:
                    for u in self.units:
                        if u["owner"] == self.player_id:
                            ux, uy = self.to_screen_coords(u["x"], u["y"])
                            if sx1 <= ux <= sx2 and sy1 <= uy <= sy2:
                                self.selected_units.add(u["id"])
            self.drag_start = None

    def handle_common_board_events(self, event):
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_m:
                self.show_minimap = not self.show_minimap
            elif event.key in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
                self.zoom_at(pygame.mouse.get_pos(), min(4.0, self.zoom * 1.25))
            elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                self.zoom_at(pygame.mouse.get_pos(), max(1.0, self.zoom / 1.25))
        elif event.type == pygame.MOUSEWHEEL:
            target_zoom = max(1.0, min(4.0, self.zoom * 1.15 if event.y > 0 else self.zoom / 1.15))
            self.zoom_at(pygame.mouse.get_pos(), target_zoom)

    def zoom_at(self, mouse_pos, new_zoom):
        wx_before, wy_before = self.to_world_coords(mouse_pos[0], mouse_pos[1])
        self.zoom = new_zoom
        board_render_size = 500.0
        scale = self.zoom * (board_render_size / WORLD_SIZE)

        cx, cy = WORLD_SIZE / 2, WORLD_SIZE / 2
        if self.player_id == 0:   rwx, rwy = cx - (wx_before - cx), cy - (wy_before - cy)
        elif self.player_id == 2: rwx, rwy = cx + (wy_before - cy), cy - (wx_before - cx)
        elif self.player_id == 3: rwx, rwy = cx - (wy_before - cy), cy + (wx_before - cx)
        else:                     rwx, rwy = wx_before, wy_before

        local_sx = mouse_pos[0] - 150
        local_sy = mouse_pos[1] - 40
        self.camera_x = rwx - (local_sx / scale)
        self.camera_y = rwy - (local_sy / scale)

        max_cam = max(0.0, WORLD_SIZE - (WORLD_SIZE / self.zoom))
        self.camera_x = max(0.0, min(max_cam, self.camera_x))
        self.camera_y = max(0.0, min(max_cam, self.camera_y))

    def to_screen_coords(self, wx, wy):
        cx, cy = WORLD_SIZE / 2, WORLD_SIZE / 2
        if self.player_id == 0:   rwx, rwy = cx - (wx - cx), cy - (wy - cy)
        elif self.player_id == 2: rwx, rwy = cx + (wy - cy), cy - (wx - cx)
        elif self.player_id == 3: rwx, rwy = cx - (wy - cy), cy + (wx - cx)
        else:                     rwx, rwy = wx, wy

        board_render_size = 500.0
        scale = self.zoom * (board_render_size / WORLD_SIZE)
        local_x = (rwx - self.camera_x) * scale
        local_y = (rwy - self.camera_y) * scale

        return 150 + local_x, 40 + local_y

    def to_world_coords(self, sx, sy):
        board_render_size = 500.0
        scale = self.zoom * (board_render_size / WORLD_SIZE)
        rwx = ((sx - 150) / scale) + self.camera_x
        rwy = ((sy - 40) / scale) + self.camera_y

        cx, cy = WORLD_SIZE / 2, WORLD_SIZE / 2
        if self.player_id == 0:   wx, wy = cx - (rwx - cx), cy - (rwy - cy)
        elif self.player_id == 2: wx, wy = cx - (rwy - cy), cy + (rwx - cx)
        elif self.player_id == 3: wx, wy = cx + (rwy - cy), cy - (rwx - cx)
        else:                     wx, wy = rwx, rwy
        return wx, wy

    def to_screen_angle(self, angle):
        if self.player_id == 0:   rot = math.pi
        elif self.player_id == 2: rot = -math.pi / 2
        elif self.player_id == 3: rot = math.pi / 2
        else:                     rot = 0.0
        return angle + rot

    def draw_board(self):
        board_rect = pygame.Rect(150, 40, 500, 500)
        SCREEN.set_clip(board_rect)

        ts_world = WORLD_SIZE / float(self.board_size)
        ts_screen = ts_world * self.zoom * (500.0 / WORLD_SIZE)
        rect_size = math.ceil(ts_screen) + 1

        for r in range(self.board_size):
            for c in range(self.board_size):
                if self.heightmap and r < len(self.heightmap) and c < len(self.heightmap[r]):
                    h = self.heightmap[r][c]
                    if h <= self.water_level:
                        color = (40, 120, 220)
                    else:
                        green_val = max(120, min(220, int(160 + h * 30)))
                        color = (40, green_val, 50)
                else:
                    color = (105, 185, 85)

                wx = c * ts_world + ts_world/2.0
                wy = r * ts_world + ts_world/2.0
                sx, sy = self.to_screen_coords(wx, wy)

                margin = rect_size
                if sx < 150 - margin or sx > 650 + margin or sy < 40 - margin or sy > 540 + margin:
                    continue

                rect = pygame.Rect(0, 0, rect_size, rect_size)
                rect.center = (int(sx), int(sy))
                pygame.draw.rect(SCREEN, color, rect)

        SCREEN.set_clip(None)

    def draw_units_and_projectiles(self):
        board_rect = pygame.Rect(150, 40, 500, 500)
        SCREEN.set_clip(board_rect)

        for p in self.projectiles:
            sx, sy = self.to_screen_coords(p["x"], p["y"])
            s_angle = self.to_screen_angle(p["angle"])
            end_x = sx + int(10 * self.zoom * math.cos(s_angle))
            end_y = sy + int(10 * self.zoom * math.sin(s_angle))
            pygame.draw.line(SCREEN, (255, 220, 0), (int(sx), int(sy)), (int(end_x), int(end_y)), 2)

        for u in self.units:
            sx, sy = self.to_screen_coords(u["x"], u["y"])
            s_angle = self.to_screen_angle(u["angle"])
            color = PLAYER_COLORS.get(u["owner"], (255, 255, 255))

            block_width = 2.4 if u["type"] in ("Queen", "Rook") else 2.0
            radius_world = (block_width / self.board_size) * WORLD_SIZE * 0.5
            draw_radius = int(radius_world * self.zoom * (500.0 / WORLD_SIZE))
            collision_radius = draw_radius

            if not u.get("is_moving", False):
                foot_offset = 0
                left_foot_x = sx - math.sin(s_angle) * (draw_radius * 0.6) + math.cos(s_angle) * foot_offset
                left_foot_y = sy + math.cos(s_angle) * (draw_radius * 0.6) + math.sin(s_angle) * foot_offset
                right_foot_x = sx + math.sin(s_angle) * (draw_radius * 0.6) - math.cos(s_angle) * foot_offset
                right_foot_y = sy - math.cos(s_angle) * (draw_radius * 0.6) - math.sin(s_angle) * foot_offset

                foot_radius = max(1, int(draw_radius * 0.25))
                pygame.draw.circle(SCREEN, (20, 20, 20), (int(left_foot_x), int(left_foot_y)), foot_radius)
                pygame.draw.circle(SCREEN, (20, 20, 20), (int(right_foot_x), int(right_foot_y)), foot_radius)

            if u["id"] in self.selected_units:
                pygame.draw.circle(SCREEN, (255, 255, 255), (int(sx), int(sy)), collision_radius + 2, 1)

            shape = u["shape"]
            draw_color = (255, 255, 255) if u.get("is_hit", False) else color
            if shape == "circle":
                pygame.draw.circle(SCREEN, draw_color, (int(sx), int(sy)), draw_radius)
            elif shape == "square":
                pygame.draw.rect(SCREEN, draw_color, (int(sx) - draw_radius, int(sy) - draw_radius, draw_radius * 2, draw_radius * 2))
            elif shape == "cross":
                th = max(2, int(draw_radius * 0.6))
                pygame.draw.rect(SCREEN, draw_color, (int(sx) - th//2, int(sy) - draw_radius, th, draw_radius * 2))
                pygame.draw.rect(SCREEN, draw_color, (int(sx) - draw_radius, int(sy) - th//2, draw_radius * 2, th))
            else:
                sides = 3 if shape == "triangle" else (5 if shape == "pentagon" else (6 if shape == "hexagon" else (8 if shape == "octagon" else 4)))
                points = [(sx + draw_radius * math.cos(s_angle + i * (2 * math.pi / sides)), sy + draw_radius * math.sin(s_angle + i * (2 * math.pi / sides))) for i in range(sides)]
                pygame.draw.polygon(SCREEN, draw_color, points)

            hp_ratio = max(0, u["hp"] / u["max_hp"])
            bar_w = max(10, draw_radius * 2)
            pygame.draw.rect(SCREEN, (255, 0, 0), (int(sx) - bar_w//2, int(sy) - draw_radius - 6, bar_w, 3))
            pygame.draw.rect(SCREEN, (0, 255, 0), (int(sx) - bar_w//2, int(sy) - draw_radius - 6, int(bar_w * hp_ratio), 3))

            stick_length = draw_radius * 1.5
            end_x = sx + math.cos(s_angle) * stick_length
            end_y = sy + math.sin(s_angle) * stick_length
            pygame.draw.line(SCREEN, (0, 0, 0), (int(sx), int(sy)), (int(end_x), int(end_y)), 2)

        SCREEN.set_clip(None)

    def draw_minimap(self):
        if not self.show_minimap: return
        mm_size = 100
        mm_x = WIDTH - mm_size - 15
        mm_y = 50

        pygame.draw.rect(SCREEN, (20, 20, 25), (mm_x, mm_y, mm_size, mm_size))
        pygame.draw.rect(SCREEN, (100, 100, 100), (mm_x, mm_y, mm_size, mm_size), 1)

        cx, cy = WORLD_SIZE / 2, WORLD_SIZE / 2
        for u in self.units:
            if self.player_id == 0:   rwx, rwy = cx - (u["x"] - cx), cy - (u["y"] - cy)
            elif self.player_id == 2: rwx, rwy = cx + (u["y"] - cy), cy - (u["x"] - cx)
            elif self.player_id == 3: rwx, rwy = cx - (u["y"] - cy), cy + (u["x"] - cx)
            else:                     rwx, rwy = u["x"], u["y"]

            ux = mm_x + int((rwx / WORLD_SIZE) * mm_size)
            uy = mm_y + int((rwy / WORLD_SIZE) * mm_size)
            col = PLAYER_COLORS.get(u["owner"], (255, 255, 255))
            pygame.draw.circle(SCREEN, col, (ux, uy), 2)

        vw = int(((WORLD_SIZE / self.zoom) / WORLD_SIZE) * mm_size)
        vh = int(((WORLD_SIZE / self.zoom) / WORLD_SIZE) * mm_size)
        vx = mm_x + int((self.camera_x / WORLD_SIZE) * mm_size)
        vy = mm_y + int((self.camera_y / WORLD_SIZE) * mm_size)
        pygame.draw.rect(SCREEN, (255, 255, 255), (vx, vy, vw, vh), 1)

    def draw_lobby(self):
        title = BIG_FONT.render("Realtime-Chess Waiting Room", True, (255, 255, 255))
        SCREEN.blit(title, (40, 15))
        score_str = " | ".join([f"P{k+1}: {v}" for k, v in sorted(self.scores.items())])
        score_txt = FONT.render(f"Scores - {score_str}", True, (200, 200, 200))
        SCREEN.blit(score_txt, (40, 40))

        settings_txt = FONT.render(f"Mode: {self.game_mode} (M) | Size: {self.board_size} (UP/DN) | Gold: ${self.starting_gold} (L/R) | Water: {self.water_rising} (W)", True, (200, 220, 100))
        SCREEN.blit(settings_txt, (40, 65))

        start_txt = FONT.render("Press SPACE to Start (Host Only)", True, (100, 255, 100))
        SCREEN.blit(start_txt, (40, 95))

        pygame.draw.rect(SCREEN, (20, 20, 25), (40, 130, 720, 400))
        for i, msg in enumerate(self.chat_messages[-15:]):
            SCREEN.blit(FONT.render(msg, True, (220, 220, 220)), (50, 140 + i * 22))

    def draw_shop(self):
        self.draw_board()
        self.draw_units_and_projectiles()
        self.draw_minimap()

        hdr = pygame.Surface((WIDTH, 40))
        hdr.fill((15, 15, 20))
        SCREEN.blit(hdr, (0, 0))
        SCREEN.blit(BIG_FONT.render(f"Buy Phase - Gold: ${self.gold}", True, (255, 215, 0)), (15, 8))
        for i, (name, cost) in enumerate(SHOP_ITEMS):
            rect = pygame.Rect(10 + i * 110, 550, 105, 38)
            pygame.draw.rect(SCREEN, (50, 50, 75), rect, border_radius=4)
            SCREEN.blit(FONT.render(f"+ {name} (${cost})", True, (255, 255, 255)), (rect.x + 4, rect.y + 10))
        ready_rect = pygame.Rect(680, 550, 110, 38)
        pygame.draw.rect(SCREEN, (0, 180, 0) if self.ready_map.get(self.player_id, False) else (190, 40, 40), ready_rect, border_radius=4)
        SCREEN.blit(BIG_FONT.render("READY", True, (255, 255, 255)), (ready_rect.x + 25, ready_rect.y + 7))

    def draw_game(self):
        header = pygame.Surface((WIDTH, 40))
        header.fill((15, 15, 20))
        SCREEN.blit(header, (0, 0))
        score_str = " | ".join([f"P{k+1}: {v}" for k, v in sorted(self.scores.items())])
        SCREEN.blit(FONT.render(f"[{self.game_mode}] {score_str} | Playing as Player {self.player_id + 1} | Zoom: {self.zoom:.2f}x (Scroll/+/-)", True, (255, 255, 255)), (15, 12))

        self.draw_board()
        self.draw_units_and_projectiles()
        self.draw_minimap()

        if self.drag_start:
            mx, my = pygame.mouse.get_pos()
            start_x, start_y = self.drag_start
            if start_y > 40 and my > 40:
                rect_x, rect_y = min(start_x, mx), min(start_y, my)
                rect_w, rect_h = abs(mx - start_x), abs(my - start_y)
                pygame.draw.rect(SCREEN, (0, 255, 0), (rect_x, rect_y, rect_w, rect_h), 1)

if __name__ == "__main__":
    ClientApp().run()

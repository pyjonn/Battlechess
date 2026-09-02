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
    "Peasant": 1.2, "Runner": 1.4, "King": 0.8, "Knight": 0.6, "Medic": 1.5, "Shieldman": 0.5
}

def play_pitched_melee(unit_type):
    vol = MELEE_PITCHES.get(unit_type, 1.0) * 0.4
    MELEE_SOUND.set_volume(min(1.0, vol))
    MELEE_SOUND.play()

WIDTH, HEIGHT = 950, 600
SCREEN = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("Realtime-Chess Client")
FONT = pygame.font.SysFont("Arial", 14, bold=True)
BIG_FONT = pygame.font.SysFont("Arial", 18, bold=True)
TITLE_FONT = pygame.font.SysFont("Arial", 22, bold=True)
FONT_CUSTOM = pygame.font.SysFont("Arial", 12, bold=True)

SHOP_ITEMS = [
    ("Peasant", 100),
    ("Archer", 150),
    ("Runner", 140),
    ("Medic", 180),
    ("Shieldman", 120),
    ("Knight", 250),
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
        self.host_id = 0
        self.particles = []
        self.chat_active = False
        self.scores = {}
        self.kills = {}
        self.usernames = {}
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
        self.right_dragging = False
        self.pan_start_pos = None
        self.pan_start_cam = None
        self.anim_tick = 0
        self.footstep_index = 0
        self.last_bow_time = 0
        self.fog_enabled = False
        self.water_enabled = False

        self.zoom = 1.0
        self.camera_x = 0.0
        self.camera_y = 0.0
        self.show_minimap = True
        self.selected_shop_item = None

        self.textures = {}
        unit_types = ["Peasant", "Archer", "Runner", "Medic", "Shieldman", "Knight", "King"]
        for u_type in unit_types:
            self.textures[u_type] = {
                "base": self.load_texture(f"textures/{u_type}.png"),
                "f": self.load_texture(f"textures/{u_type}f.png"),
                "b": self.load_texture(f"textures/{u_type}b.png")
            }

    def load_texture(self, path):
        if os.path.exists(path):
            try:
                return pygame.image.load(path).convert_alpha()
            except Exception as e:
                print(f"Failed to load {path}: {e}")
        return None

    def get_player_color(self, owner):
        if self.game_mode == "2v2":
            team_colors = {
                0: (60, 120, 240),
                2: (130, 190, 255),
                1: (220, 60, 60),
                3: (255, 140, 120)
            }
            return team_colors.get(owner, (255, 255, 255))
        else:
            ffa_colors = {
                0: (60, 120, 240),
                1: (220, 60, 60),
                2: (60, 220, 60),
                3: (220, 220, 60)
            }
            return ffa_colors.get(owner, (255, 255, 255))

    def is_tile_visible(self, wx, wy, my_units=None):
        if not self.fog_enabled or self.game_state == "SHOP":
            return True
        if my_units is None:
            my_units = [u for u in self.units if u["owner"] == self.player_id]
        for u in my_units:
            if (wx - u["x"])**2 + (wy - u["y"])**2 <= 40000.0:
                return True
        return False

    def update_default_zoom(self):
        self.zoom = 1.0
        self.camera_x = 0.0
        self.camera_y = 0.0

    def run(self):
        clock = pygame.time.Clock()
        while True:
            dt = clock.tick(60) / 1000.0
            self.anim_tick += 1

            alive_particles = []
            for p in self.particles:
                p["x"] += p["vx"] * dt
                p["y"] += p["vy"] * dt
                p["life"] -= dt
                if p["life"] > 0:
                    alive_particles.append(p)
            self.particles = alive_particles

            if self.state == "CONNECTED" and self.game_state in ("SHOP", "IN_GAME"):
                keys = pygame.key.get_pressed()
                pan_speed = 400.0 * dt / self.zoom
                max_cam = max(0.0, WORLD_SIZE - (WORLD_SIZE / self.zoom))

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
            self.host_id = msg.get("host_id", 0)
            self.scores = {int(k): v for k, v in msg["scores"].items()}
            self.kills = {int(k): v for k, v in msg.get("kills", {}).items()}
            self.usernames = {int(k): v for k, v in msg.get("usernames", {}).items()}
            self.board_size = msg["board_size"]
            self.heightmap = msg.get("heightmap", [])
            self.water_level = msg.get("water_level", -0.5)
            self.starting_gold = msg["starting_gold"]
            self.game_mode = msg.get("game_mode", "FFA")
            self.fog_enabled = msg.get("fog_enabled", False)
            self.update_default_zoom()
        elif mtype == "USERNAMES_UPDATE":
            self.usernames = {int(k): v for k, v in msg["usernames"].items()}
            if "host_id" in msg:
                self.host_id = msg["host_id"]
        elif mtype == "HOST_UPDATE":
            self.host_id = msg["host_id"]
        elif mtype == "PLAYER_DISCONNECT":
            self.scores = {int(k): v for k, v in msg["scores"].items()}
            self.kills = {int(k): v for k, v in msg.get("kills", {}).items()}
            self.game_state = "LOBBY"
            self.state = "CONNECTED"
            self.units = []
            self.projectiles = []  # Clear projectiles on disconnect
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
            self.fog_enabled = msg.get("fog_enabled", self.fog_enabled)
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
            if "kills" in msg:
                self.kills = {int(k): v for k, v in msg["kills"].items()}
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
            if "kills" in msg:
                self.kills = {int(k): v for k, v in msg["kills"].items()}
            self.update_default_zoom()
        elif mtype == "ATTACK_SOUND":
            utype = msg.get("unit_type")
            if utype == "Archer":
                now = time.time()
                if now - self.last_bow_time > 0.05:
                    if BOW_SOUND:
                        BOW_SOUND.set_volume(random.uniform(0.2, 0.4))
                        BOW_SOUND.play()
                    self.last_bow_time = now
            else:
                play_pitched_melee(utype)
        elif mtype == "GAME_UPDATE":
            old_units = {u["id"]: u for u in self.units}
            old_positions = {u["id"]: (u["x"], u["y"]) for u in self.units}

            for u in msg["units"]:
                if u["id"] in old_positions:
                    ox, oy = old_positions[u["id"]]
                    u["x"] = ox * 0.4 + u["x"] * 0.6
                    u["y"] = oy * 0.4 + u["y"] * 0.6

            self.units = msg["units"]

            new_unit_ids = {u["id"] for u in self.units}
            for uid, old_u in old_units.items():
                if uid not in new_unit_ids:
                    rad = old_u.get("draw_radius", 25)
                    for _ in range(15):
                        angle = random.uniform(0, math.pi * 2)
                        speed = random.uniform(rad * 0.8, rad * 3.2)
                        life = rad * 0.02
                        self.particles.append({
                            "x": old_u["x"], "y": old_u["y"],
                            "vx": math.cos(angle) * speed,
                            "vy": math.sin(angle) * speed,
                            "life": life, "max_life": life,
                            "color": self.get_player_color(old_u["owner"])
                        })
            self.projectiles = msg.get("projectiles", [])
            self.water_level = msg.get("water_level", self.water_level)
            if "kills" in msg:
                self.kills = {int(k): v for k, v in msg["kills"].items()}

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
            if "kills" in msg:
                self.kills = {int(k): v for k, v in msg["kills"].items()}
            self.game_state = "LOBBY"
            self.projectiles = []  # Clear projectiles when game ends
            winner = msg['winner']
            if self.game_mode == "2v2":
                self.chat_messages.append(f"System: Team {winner + 1} won!")
            else:
                pname = self.usernames.get(winner, f"Player {winner + 1}")
                self.chat_messages.append(f"System: {pname} won!")

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
        t = TITLE_FONT.render("Realtime Chess - Main Menu", True, (255, 255, 255))
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
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if pygame.Rect(40, 540, 870, 35).collidepoint(event.pos):
                self.chat_active = True
            else:
                self.chat_active = False

        if event.type == pygame.KEYDOWN:
            if self.chat_active:
                if event.key == pygame.K_RETURN:
                    if self.chat_input.strip():
                        self.send({"type": "CHAT", "text": self.chat_input})
                        self.chat_input = ""
                    self.chat_active = False
                elif event.key == pygame.K_BACKSPACE:
                    self.chat_input = self.chat_input[:-1]
                elif event.unicode.isprintable() and len(self.chat_input) < 40:
                    self.chat_input += event.unicode
            else:
                if self.player_id == self.host_id:
                    if event.key == pygame.K_UP:
                        self.send({"type": "SET_BOARD_SIZE", "size": min(128, self.board_size + 2)})
                    elif event.key == pygame.K_DOWN:
                        self.send({"type": "SET_BOARD_SIZE", "size": max(12, self.board_size - 2)})
                    elif event.key == pygame.K_RIGHT:
                        self.send({"type": "SET_STARTING_GOLD", "starting_gold": min(10000, self.starting_gold + 100)})
                    elif event.key == pygame.K_LEFT:
                        self.send({"type": "SET_STARTING_GOLD", "starting_gold": max(100, self.starting_gold - 100)})
                    elif event.key == pygame.K_w:
                        self.send({"type": "SET_WATER_RISING", "rising": not self.water_rising})
                    elif event.key == pygame.K_f:
                        self.send({"type": "TOGGLE_FOG"})
                    elif event.key == pygame.K_m:
                        self.send({"type": "TOGGLE_MODE"})
                    elif event.key == pygame.K_SPACE:
                        self.send({"type": "START_GAME"})

    def handle_shop_events(self, event):
        self.handle_common_board_events(event)
        if event.type == pygame.MOUSEBUTTONDOWN:
            mx, my = event.pos

            if event.button == 1:
                # Restrict buy clicks strictly to the visual board area
                if 260 <= mx <= 760 and 40 <= my <= 540 and self.selected_shop_item:
                    wmx, wmy = self.to_world_coords(mx, my)

                    valid_side = True
                    if self.player_id == 0 and wmy < 400: valid_side = False
                    elif self.player_id == 1 and wmy > 400: valid_side = False
                    elif self.player_id == 2 and wmx > 400: valid_side = False
                    elif self.player_id == 3 and wmx < 400: valid_side = False

                    if valid_side:
                        self.send({
                            "type": "BUY_UNIT",
                            "unit_type": self.selected_shop_item,
                            "x": wmx, "y": wmy
                        })

                for i, (name, cost) in enumerate(SHOP_ITEMS):
                    if pygame.Rect(260 + i * 95, 550, 90, 38).collidepoint(mx, my):
                        self.selected_shop_item = name
                        break

                if pygame.Rect(830, 550, 110, 38).collidepoint(mx, my):
                    self.send({"type": "READY_SHOP"})

            elif event.button == 3:
                # Restrict sell clicks strictly to the visual board area
                if 260 <= mx <= 760 and 40 <= my <= 540:
                    wmx, wmy = self.to_world_coords(mx, my)
                    for u in self.units:
                        if u["owner"] == self.player_id and u["type"] != "King":
                            if math.hypot(u["x"] - wmx, u["y"] - wmy) < 25:
                                self.send({"type": "SELL_UNIT", "unit_id": u["id"]})
                                break

    def handle_game_events(self, event):
        self.handle_common_board_events(event)
        if event.type == pygame.MOUSEBUTTONDOWN:
            smx, smy = event.pos
            if event.button == 1:
                if smx > 250 and smy > 40:
                    self.drag_start = (smx, smy)

            elif event.button == 3:
                if smx > 250 and smy > 40 and self.selected_units:
                    wmx, wmy = self.to_world_coords(smx, smy)
                    is_shift = bool(pygame.key.get_mods() & pygame.KMOD_SHIFT)

                    target_unit_id = None
                    for u in self.units:
                        u_blocks = 2.4 if u["type"] in ("Knight", "Shieldman") else 2.0
                        u_radius_world = (u_blocks / self.board_size) * WORLD_SIZE * 0.5
                        if math.hypot(u["x"] - wmx, u["y"] - wmy) < u_radius_world:
                            target_unit_id = u["id"]
                            break

                    self.send({
                        "type": "COMMAND",
                        "unit_ids": list(self.selected_units),
                        "target_pos": [wmx, wmy],
                        "target_unit": target_unit_id,
                        "append_path": is_shift
                    })

        elif event.type == pygame.MOUSEBUTTONUP:
            smx, smy = event.pos
            if event.button == 1:
                if self.drag_start:
                    start_x, start_y = self.drag_start
                    self.drag_start = None

                    if smx > 250 and smy > 40 and start_x > 250 and start_y > 40:
                        is_shift = bool(pygame.key.get_mods() & pygame.KMOD_SHIFT)

                        if abs(smx - start_x) < 5 and abs(smy - start_y) < 5:
                            wmx, wmy = self.to_world_coords(smx, smy)
                            if not is_shift:
                                self.selected_units.clear()
                            for u in self.units:
                                if u["owner"] == self.player_id:
                                    u_blocks = 2.4 if u["type"] in ("Knight", "Shieldman") else 2.0
                                    u_radius_world = (u_blocks / self.board_size) * WORLD_SIZE * 0.5
                                    if math.hypot(u["x"] - wmx, u["y"] - wmy) < (u_radius_world + 8 / self.zoom):
                                        self.selected_units.add(u["id"])
                        else:
                            if not is_shift:
                                self.selected_units.clear()
                            min_x, max_x = min(start_x, smx), max(start_x, smx)
                            min_y, max_y = min(start_y, smy), max(start_y, smy)
                            select_rect = pygame.Rect(min_x, min_y, max_x - min_x, max_y - min_y)

                            for u in self.units:
                                if u["owner"] == self.player_id:
                                    usx, usy = self.to_screen_coords(u["x"], u["y"])
                                    if select_rect.collidepoint(usx, usy):
                                        self.selected_units.add(u["id"])

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
        elif event.type == pygame.MOUSEBUTTONDOWN:
            if event.button == 2:
                smx, smy = event.pos
                if smx > 250 and smy > 40:
                    self.right_dragging = True
                    self.pan_start_pos = (smx, smy)
                    self.pan_start_cam = (self.camera_x, self.camera_y)
        elif event.type == pygame.MOUSEBUTTONUP:
            if event.button == 2:
                self.right_dragging = False
        elif event.type == pygame.MOUSEMOTION:
            if getattr(self, "right_dragging", False) and getattr(self, "pan_start_pos", None) and getattr(self, "pan_start_cam", None):
                mx, my = event.pos
                dx = mx - self.pan_start_pos[0]
                dy = my - self.pan_start_pos[1]
                scale = self.zoom * (500.0 / WORLD_SIZE)
                max_cam = max(0.0, WORLD_SIZE - (WORLD_SIZE / self.zoom))
                self.camera_x = max(0.0, min(max_cam, self.pan_start_cam[0] - dx / scale))
                self.camera_y = max(0.0, min(max_cam, self.pan_start_cam[1] - dy / scale))

    def zoom_at(self, mouse_pos, new_zoom):
            wx_before, wy_before = self.to_world_coords(mouse_pos[0], mouse_pos[1])
            self.zoom = new_zoom
            board_render_size = 500.0
            scale = self.zoom * (board_render_size / WORLD_SIZE)

            cx, cy = WORLD_SIZE / 2, WORLD_SIZE / 2
            if self.player_id == 1:   rwx, rwy = cx - (wx_before - cx), cy - (wy_before - cy)
            elif self.player_id == 2: rwx, rwy = cx + (wy_before - cy), cy - (wx_before - cx)
            elif self.player_id == 3: rwx, rwy = cx - (wy_before - cy), cy + (wx_before - cx)
            else:                     rwx, rwy = wx_before, wy_before

            local_sx = mouse_pos[0] - 260
            local_sy = mouse_pos[1] - 40
            self.camera_x = rwx - (local_sx / scale)
            self.camera_y = rwy - (local_sy / scale)

            max_cam = max(0.0, WORLD_SIZE - (WORLD_SIZE / self.zoom))
            self.camera_x = max(0.0, min(max_cam, self.camera_x))
            self.camera_y = max(0.0, min(max_cam, self.camera_y))

    def to_screen_coords(self, wx, wy):
            cx, cy = WORLD_SIZE / 2, WORLD_SIZE / 2
            if self.player_id == 1:   rwx, rwy = cx - (wx - cx), cy - (wy - cy)
            elif self.player_id == 2: rwx, rwy = cx + (wy - cy), cy - (wx - cx)
            elif self.player_id == 3: rwx, rwy = cx - (wy - cy), cy + (wx - cx)
            else:                     rwx, rwy = wx, wy

            board_render_size = 500.0
            scale = self.zoom * (board_render_size / WORLD_SIZE)
            local_x = (rwx - self.camera_x) * scale
            local_y = (rwy - self.camera_y) * scale

            return 260 + local_x, 40 + local_y

    def to_world_coords(self, sx, sy):
            board_render_size = 500.0
            scale = self.zoom * (board_render_size / WORLD_SIZE)
            rwx = ((sx - 260) / scale) + self.camera_x
            rwy = ((sy - 40) / scale) + self.camera_y

            cx, cy = WORLD_SIZE / 2, WORLD_SIZE / 2
            if self.player_id == 1:   wx, wy = cx - (rwx - cx), cy - (rwy - cy)
            elif self.player_id == 2: wx, wy = cx - (rwy - cy), cy + (rwx - cx)
            elif self.player_id == 3: wx, wy = cx + (rwy - cy), cy - (rwx - cx)
            else:                     wx, wy = rwx, rwy
            return wx, wy

    def to_screen_angle(self, angle):
            if self.player_id == 1:   rot = math.pi
            elif self.player_id == 2: rot = -math.pi / 2
            elif self.player_id == 3: rot = math.pi / 2
            else:                     rot = 0.0
            return angle + rot

    def draw_board(self):
                board_rect = pygame.Rect(260, 40, 500, 500)
                SCREEN.set_clip(board_rect)

                ts_world = WORLD_SIZE / float(self.board_size)
                ts_screen = ts_world * self.zoom * (500.0 / WORLD_SIZE)
                rect_size = math.ceil(ts_screen) + 1

                COLOR_WHITE = (255, 255, 255)
                COLOR_GREY  = (100, 105, 120)
                COLOR_DARK_GREEN  = (0, 100, 0)
                COLOR_LIGHT_GREEN = (140, 230, 80)

                my_units = [u for u in self.units if u["owner"] == self.player_id]

                for r in range(self.board_size):
                    for c in range(self.board_size):
                        wx = c * ts_world + ts_world / 2.0
                        wy = r * ts_world + ts_world / 2.0

                        sx, sy = self.to_screen_coords(wx, wy)
                        margin = rect_size

                        if sx < 260 - margin or sx > 760 + margin or sy < 40 - margin or sy > 540 + margin:
                            continue

                        if self.heightmap and r < len(self.heightmap) and c < len(self.heightmap[r]):
                            h = self.heightmap[r][c]

                            if h <= self.water_level:
                                color = (0, 120, 245)
                            else:
                                norm_h = max(0.0, min(1.0, (h - self.water_level) / (3.5 - self.water_level)))
                                color = (
                                    int(COLOR_DARK_GREEN[0] + norm_h * (COLOR_LIGHT_GREEN[0] - COLOR_DARK_GREEN[0])),
                                    int(COLOR_DARK_GREEN[1] + norm_h * (COLOR_LIGHT_GREEN[1] - COLOR_DARK_GREEN[1])),
                                    int(COLOR_DARK_GREEN[2] + norm_h * (COLOR_LIGHT_GREEN[2] - COLOR_DARK_GREEN[2]))
                                )
                        else:
                            color = COLOR_DARK_GREEN

                        if not self.is_tile_visible(wx, wy, my_units):
                            color = (int(color[0] * 0.2), int(color[1] * 0.2), int(color[2] * 0.2))

                        if self.game_state == "SHOP":
                            is_my_side = True
                            if self.player_id == 0 and wy < 400: is_my_side = False
                            elif self.player_id == 1 and wy > 400: is_my_side = False
                            elif self.player_id == 2 and wx > 400: is_my_side = False
                            elif self.player_id == 3 and wx < 400: is_my_side = False

                            if not is_my_side:
                                gray = int(0.3 * color[0] + 0.59 * color[1] + 0.11 * color[2])
                                color = (int(gray * 0.4 + color[0] * 0.2), int(gray * 0.4 + color[1] * 0.2), int(gray * 0.4 + color[2] * 0.2))

                        rect = pygame.Rect(0, 0, rect_size, rect_size)
                        rect.center = (int(sx), int(sy))
                        pygame.draw.rect(SCREEN, color, rect)

                SCREEN.set_clip(None)

    def draw_units_and_projectiles(self):
        board_rect = pygame.Rect(260, 40, 500, 500)
        SCREEN.set_clip(board_rect)
        my_units = [u for u in self.units if u["owner"] == self.player_id]

        for u in self.units:
            if u["owner"] == self.player_id and u.get("is_moving", False):
                start_sx, start_sy = self.to_screen_coords(u["x"], u["y"])
                path_points = [(start_sx, start_sy)]

                if "target_x" in u and "target_y" in u:
                    tsx, tsy = self.to_screen_coords(u["target_x"], u["target_y"])
                    path_points.append((tsx, tsy))

                waypoints = u.get("waypoints", [])
                for wp in waypoints[1:]:
                    wsx, wsy = self.to_screen_coords(wp[0], wp[1])
                    path_points.append((wsx, wsy))

                if len(path_points) > 1:
                    is_selected = u["id"] in self.selected_units
                    line_color = (0, 255, 120) if is_selected else (140, 180, 140)
                    line_width = 2 if is_selected else 1

                    pygame.draw.lines(
                        SCREEN,
                        line_color,
                        False,
                        [(int(px), int(py)) for px, py in path_points],
                        line_width
                    )

                    end_x, end_y = path_points[-1]
                    pygame.draw.circle(SCREEN, line_color, (int(end_x), int(end_y)), 4, 1)

        for p in self.particles:
            if self.is_tile_visible(p["x"], p["y"], my_units):
                sx, sy = self.to_screen_coords(p["x"], p["y"])
                radius = max(1, int(6 * (p["life"] / p["max_life"]) * self.zoom))
                pygame.draw.circle(SCREEN, p["color"], (int(sx), int(sy)), radius)

        for p in self.projectiles:
            if self.is_tile_visible(p["x"], p["y"], my_units):
                sx, sy = self.to_screen_coords(p["x"], p["y"])
                s_angle = self.to_screen_angle(p["angle"])
                end_x = sx + int(10 * self.zoom * math.cos(s_angle))
                end_y = sy + int(10 * self.zoom * math.sin(s_angle))
                pygame.draw.line(SCREEN, (255, 220, 0), (int(sx), int(sy)), (int(end_x), int(end_y)), 2)

        for u in self.units:
            if self.game_state == "SHOP" and u["owner"] != self.player_id and u["type"] != "King":
                continue

            if not self.is_tile_visible(u["x"], u["y"], my_units):
                continue

            sx, sy = self.to_screen_coords(u["x"], u["y"])
            s_angle = self.to_screen_angle(u["angle"])
            color = self.get_player_color(u["owner"])

            block_width = 2.4 if u["type"] in ("Knight", "Shieldman") else 2.0
            radius_world = (block_width / self.board_size) * WORLD_SIZE * 0.5
            draw_radius = int(radius_world * self.zoom * (500.0 / WORLD_SIZE))
            collision_radius = draw_radius

            if u["id"] in self.selected_units:
                pygame.draw.circle(SCREEN, (255, 255, 255), (int(sx), int(sy)), collision_radius + 2, 1)

            img_size = draw_radius * 2
            unit_tex = self.textures.get(u["type"])
            current_img = None

            if unit_tex:
                if u.get("is_moving", False):
                    if (self.anim_tick // 15) % 2 == 0:
                        current_img = unit_tex["f"]
                    else:
                        current_img = unit_tex["b"]

                    if current_img is None:
                        current_img = unit_tex["base"]
                else:
                    current_img = unit_tex["base"]

            if current_img:
                scaled_img = pygame.transform.scale(current_img, (img_size, img_size))
                deg_angle = math.degrees(-s_angle) - 90
                rotated_img = pygame.transform.rotate(scaled_img, deg_angle)
                rect = rotated_img.get_rect(center=(int(sx), int(sy)))
                SCREEN.blit(rotated_img, rect.topleft)
            else:
                draw_color = (255, 255, 255) if u.get("is_hit", False) else color
                shape = u["shape"]

                if shape == "circle":
                    pygame.draw.circle(SCREEN, draw_color, (int(sx), int(sy)), draw_radius)
                elif shape == "square":
                    rect_obj = (int(sx) - draw_radius, int(sy) - draw_radius, draw_radius * 2, draw_radius * 2)
                    pygame.draw.rect(SCREEN, draw_color, rect_obj)
                elif shape == "cross":
                    th = max(2, int(draw_radius * 0.6))
                    rect1 = (int(sx) - th//2, int(sy) - draw_radius, th, draw_radius * 2)
                    rect2 = (int(sx) - draw_radius, int(sy) - th//2, draw_radius * 2, th)
                    pygame.draw.rect(SCREEN, draw_color, rect1)
                    pygame.draw.rect(SCREEN, draw_color, rect2)
                else:
                    sides = 3 if shape == "triangle" else (5 if shape == "pentagon" else (6 if shape == "hexagon" else (8 if shape == "octagon" else 4)))
                    points = [(sx + draw_radius * math.cos(s_angle + i * (2 * math.pi / sides)), sy + draw_radius * math.sin(s_angle + i * (2 * math.pi / sides))) for i in range(sides)]
                    pygame.draw.polygon(SCREEN, draw_color, points)

            # Draw the Team-Colored Shape Outline Around the Unit
            shape = u["shape"]
            ring_color = (255, 255, 255) if u.get("is_hit", False) else color
            outline_width = 2
            out_radius = draw_radius + 2

            if shape == "circle":
                pygame.draw.circle(SCREEN, ring_color, (int(sx), int(sy)), out_radius, outline_width)
            elif shape == "square":
                rect_obj = (int(sx) - out_radius, int(sy) - out_radius, out_radius * 2, out_radius * 2)
                pygame.draw.rect(SCREEN, ring_color, rect_obj, outline_width)
            elif shape == "cross":
                th = max(2, int(out_radius * 0.6))
                rect1 = (int(sx) - th//2, int(sy) - out_radius, th, out_radius * 2)
                rect2 = (int(sx) - out_radius, int(sy) - th//2, out_radius * 2, th)
                pygame.draw.rect(SCREEN, ring_color, rect1, outline_width)
                pygame.draw.rect(SCREEN, ring_color, rect2, outline_width)
            else:
                sides = 3 if shape == "triangle" else (5 if shape == "pentagon" else (6 if shape == "hexagon" else (8 if shape == "octagon" else 4)))
                points = [(sx + out_radius * math.cos(s_angle + i * (2 * math.pi / sides)), sy + out_radius * math.sin(s_angle + i * (2 * math.pi / sides))) for i in range(sides)]
                pygame.draw.polygon(SCREEN, ring_color, points, outline_width)

            hp_ratio = max(0, u["hp"] / u["max_hp"])
            bar_w = max(10, draw_radius * 2)
            pygame.draw.rect(SCREEN, (255, 0, 0), (int(sx) - bar_w//2, int(sy) - draw_radius - 6, bar_w, 3))
            pygame.draw.rect(SCREEN, (0, 255, 0), (int(sx) - bar_w//2, int(sy) - draw_radius - 6, int(bar_w * hp_ratio), 3))

            stick_length = draw_radius * 1.5
            end_x = sx + math.cos(s_angle) * stick_length
            end_y = sy + math.sin(s_angle) * stick_length
            pygame.draw.line(SCREEN, (0, 0, 0), (int(sx), int(sy)), (int(end_x), int(end_y)), 2)

            if u["type"] == "King":
                p_name = self.usernames.get(u["owner"], f"Player {u['owner'] + 1}")
                tag_surf = FONT.render(f"👑 {p_name}", True, (255, 255, 255))
                bg_rect = pygame.Rect(0, 0, tag_surf.get_width() + 8, tag_surf.get_height() + 4)
                bg_rect.center = (int(sx), int(sy) - draw_radius - 18)
                pygame.draw.rect(SCREEN, (0, 0, 0, 180), bg_rect, border_radius=3)
                pygame.draw.rect(SCREEN, color, bg_rect, width=1, border_radius=3)
                SCREEN.blit(tag_surf, (bg_rect.x + 4, bg_rect.y + 2))

        SCREEN.set_clip(None)

    def draw_sidebar(self):
        sidebar_rect = pygame.Rect(0, 0, 250, HEIGHT)
        pygame.draw.rect(SCREEN, (20, 22, 28), sidebar_rect)
        pygame.draw.line(SCREEN, (50, 55, 65), (250, 0), (250, HEIGHT), 2)

        stitle = TITLE_FONT.render("SCOREBOARD", True, (240, 240, 240))
        SCREEN.blit(stitle, (20, 15))

        mode_str = f"Mode: {self.game_mode}"
        win_str = "Win: LAST_MAN_STANDING"

        SCREEN.blit(FONT.render(mode_str, True, (160, 160, 170)), (20, 45))
        SCREEN.blit(FONT.render(win_str, True, (160, 160, 170)), (20, 65))

        pygame.draw.line(SCREEN, (40, 45, 55), (15, 90), (235, 90), 1)

        y_offset = 105
        active_pids = sorted(self.usernames.keys())
        for p_id in active_pids:
            p_color = self.get_player_color(p_id)
            p_name = self.usernames.get(p_id, f"Player {p_id + 1}")
            p_wins = self.scores.get(p_id, 0)
            p_kills = self.kills.get(p_id, 0)

            card_rect = pygame.Rect(15, y_offset, 220, 50)
            bg_col = (35, 38, 48) if p_id == self.player_id else (26, 28, 35)
            pygame.draw.rect(SCREEN, bg_col, card_rect, border_radius=5)
            pygame.draw.rect(SCREEN, p_color, card_rect, width=2, border_radius=5)

            team_lbl = f" [T{p_id % 2 + 1}]" if self.game_mode == "2v2" else ""
            name_txt = FONT.render(f"{p_name}{team_lbl}", True, (255, 255, 255))
            stats_txt = FONT.render(f"Wins:{p_wins} | Kills:{p_kills}", True, (255, 215, 0))

            SCREEN.blit(name_txt, (25, y_offset + 8))
            SCREEN.blit(stats_txt, (25, y_offset + 28))

            y_offset += 60

    def draw_minimap(self):
        if not self.show_minimap: return
        mm_size = 100
        mm_x = WIDTH - mm_size - 15
        mm_y = 50

        pygame.draw.rect(SCREEN, (20, 20, 25), (mm_x, mm_y, mm_size, mm_size))
        pygame.draw.rect(SCREEN, (100, 100, 100), (mm_x, mm_y, mm_size, mm_size), 1)

        cx, cy = WORLD_SIZE / 2, WORLD_SIZE / 2

        my_units = [u for u in self.units if u["owner"] == self.player_id]

        for u in self.units:
            if self.game_state == "SHOP" and u["owner"] != self.player_id and u["type"] != "King":
                continue

            if not self.is_tile_visible(u["x"], u["y"], my_units):
                continue

            if self.player_id == 0:   rwx, rwy = cx - (u["x"] - cx), cy - (u["y"] - cy)
            elif self.player_id == 2: rwx, rwy = cx + (u["y"] - cy), cy - (u["x"] - cx)
            elif self.player_id == 3: rwx, rwy = cx - (u["y"] - cy), cy + (u["x"] - cx)
            else:                     rwx, rwy = u["x"], u["y"]

            ux = mm_x + int((rwx / WORLD_SIZE) * mm_size)
            uy = mm_y + int((rwy / WORLD_SIZE) * mm_size)
            col = self.get_player_color(u["owner"])
            pygame.draw.circle(SCREEN, col, (ux, uy), 2)

        vw = int(((WORLD_SIZE / self.zoom) / WORLD_SIZE) * mm_size)
        vh = int(((WORLD_SIZE / self.zoom) / WORLD_SIZE) * mm_size)
        vx = mm_x + int((self.camera_x / WORLD_SIZE) * mm_size)
        vy = mm_y + int((self.camera_y / WORLD_SIZE) * mm_size)
        pygame.draw.rect(SCREEN, (255, 255, 255), (vx, vy, vw, vh), 1)

    def draw_lobby(self):
        SCREEN.fill((30, 30, 35))
        title = TITLE_FONT.render("Realtime-Chess Waiting Room", True, (255, 255, 255))
        SCREEN.blit(title, (40, 15))

        score_parts = []
        for k in sorted(self.usernames.keys()):
            p_name = self.usernames.get(k, f"P{k+1}")
            score_parts.append(f"{p_name}: {self.scores.get(k, 0)}W/{self.kills.get(k, 0)}K")
        score_str = " | ".join(score_parts)
        score_txt = FONT.render(f"Stats (Wins/Kills) - {score_str}", True, (200, 200, 200))
        SCREEN.blit(score_txt, (40, 42))

        pygame.draw.rect(SCREEN, (30, 30, 35), (40, 65, 870, 25))

        settings_txt = FONT.render(f"Mode: {self.game_mode} (M) | Size: {self.board_size} (UP/DN) | Gold: ${self.starting_gold} (L/R) | Water: {self.water_rising} (W) | Fog: {self.fog_enabled} (F)", True, (200, 220, 100))
        SCREEN.blit(settings_txt, (40, 65))

        is_host = (self.player_id == self.host_id)
        host_status_str = "Press SPACE to Start (Host Only)" if is_host else "Waiting for Host to start..."
        host_color = (100, 255, 100) if is_host else (200, 200, 100)
        start_txt = FONT.render(host_status_str, True, host_color)
        SCREEN.blit(start_txt, (40, 95))

        pygame.draw.rect(SCREEN, (20, 20, 25), (40, 130, 870, 400))
        for i, msg in enumerate(self.chat_messages[-16:]):
            SCREEN.blit(FONT.render(msg, True, (220, 220, 220)), (50, 140 + i * 22))

        chat_rect = pygame.Rect(40, 540, 870, 35)
        chat_col = (80, 120, 220) if self.chat_active else (50, 50, 70)
        pygame.draw.rect(SCREEN, chat_col, chat_rect, border_radius=6)

        if self.chat_active:
            pygame.draw.rect(SCREEN, (255, 255, 255), chat_rect, width=2, border_radius=6)

        prompt = "Chat: " if self.chat_active else "Click here to chat..."
        text_surf = FONT.render(prompt + self.chat_input, True, (255, 255, 255))
        SCREEN.blit(text_surf, (50, 548))

    def draw_shop(self):
        self.draw_sidebar()
        self.draw_board()
        self.draw_units_and_projectiles()
        self.draw_minimap()

        hdr = pygame.Surface((WIDTH - 250, 40))
        hdr.fill((15, 15, 20))
        SCREEN.blit(hdr, (250, 0))
        SCREEN.blit(TITLE_FONT.render(f"Buy Phase - Gold: ${self.gold}", True, (255, 215, 0)), (265, 8))

        for i, (name, cost) in enumerate(SHOP_ITEMS):
            rect = pygame.Rect(260 + i * 95, 550, 90, 38)
            bg_color = (80, 120, 80) if getattr(self, "selected_shop_item", None) == name else (50, 50, 75)
            pygame.draw.rect(SCREEN, bg_color, rect, border_radius=4)
            if name == "Shieldman":
                SCREEN.blit(FONT_CUSTOM.render(f"+ Shieldman", True, (255, 255, 255)), (rect.x + 4, rect.y + 4))
            else:
                SCREEN.blit(FONT_CUSTOM.render(f"+ {name}", True, (255, 255, 255)), (rect.x + 4, rect.y + 4))
            SCREEN.blit(FONT_CUSTOM.render(f"${cost}", True, (255, 215, 0)), (rect.x + 4, rect.y + 20))
        ready_rect = pygame.Rect(830, 550, 110, 38)
        pygame.draw.rect(SCREEN, (0, 180, 0) if self.ready_map.get(self.player_id, False) else (190, 40, 40), ready_rect, border_radius=4)
        SCREEN.blit(FONT_CUSTOM.render("READY", True, (255, 255, 255)), (ready_rect.x + 25, ready_rect.y + 7))

    def draw_game(self):
        self.draw_sidebar()

        header = pygame.Surface((WIDTH - 250, 40))
        header.fill((15, 15, 20))
        SCREEN.blit(header, (250, 0))

        pname = self.usernames.get(self.player_id, f"Player {self.player_id + 1}")
        SCREEN.blit(FONT.render(f"Playing as: {pname} | Zoom: {self.zoom:.2f}x (Scroll/+/-)", True, (255, 255, 255)), (265, 12))

        self.draw_board()
        self.draw_units_and_projectiles()
        self.draw_minimap()

        if self.drag_start:
            mx, my = pygame.mouse.get_pos()
            start_x, start_y = self.drag_start
            if start_x > 250 and mx > 250 and start_y > 40 and my > 40:
                rect_x, rect_y = min(start_x, mx), min(start_y, my)
                rect_w, rect_h = abs(mx - start_x), abs(my - start_y)
                pygame.draw.rect(SCREEN, (0, 255, 0), (rect_x, rect_y, rect_w, rect_h), 1)

if __name__ == "__main__":
    ClientApp().run()

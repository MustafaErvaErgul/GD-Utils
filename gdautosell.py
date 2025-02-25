import ctypes
import sys
import time
import threading
import configparser
import os
from PIL import Image, ImageDraw
import pystray

# ================= CONFIGURATION FROM config.ini =================
config = configparser.ConfigParser()
script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
config_path = os.path.join(script_dir, "config.ini")

if not os.path.exists(config_path):
    print("config.ini not found in script directory!")
    sys.exit(1)

config.read(config_path)

# Load general settings
DEBUG = config.getboolean("General", "DEBUG", fallback=False)

# ---------------- Parse Keyboard Shortcuts ----------------
def parse_key(key_str):
    """Converts a key string like 'F2' to a virtual-key code (F1=0x70)."""
    key_str = key_str.strip().upper()
    if key_str.startswith("F"):
        try:
            num = int(key_str[1:])
            return 0x70 + (num - 1)
        except:
            return None
    return None

SELL_ALL_KEY          = parse_key(config.get("Shortcuts", "SELL_ALL", fallback="F2"))
SELL_SECONDARY_KEY    = parse_key(config.get("Shortcuts", "SELL_SECONDARY", fallback="F3"))
DISMANTLE_ALL_KEY     = parse_key(config.get("Shortcuts", "DISMANTLE_ALL", fallback="F4"))
DISMANTLE_SECONDARY_KEY = parse_key(config.get("Shortcuts", "DISMANTLE_SECONDARY", fallback="F5"))
GLOBAL_EXIT_KEY       = parse_key(config.get("Shortcuts", "GLOBAL_EXIT", fallback="F10"))

# ---------------- Read UI Coordinates ----------------
def parse_point(value):
    parts = value.split(',')
    return (int(parts[0].strip()), int(parts[1].strip()))

TRANSMUTE_TAB_LOCATION     = parse_point(config.get("Coordinates", "TRANSMUTE_TAB_LOCATION"))
DISMANTLE_TAB_LOCATION     = parse_point(config.get("Coordinates", "DISMANTLE_TAB_LOCATION"))
DISMANTLE_ITEM_LOCATION    = parse_point(config.get("Coordinates", "DISMANTLE_ITEM_LOCATION"))
DISMANTLE_BUTTON_LOCATION  = parse_point(config.get("Coordinates", "DISMANTLE_BUTTON_LOCATION"))
CONFIRM_DISMANTLE_LOCATION = parse_point(config.get("Coordinates", "CONFIRM_DISMANTLE_LOCATION"))

# ---------------- Read Sleep Constants ----------------
SLEEP_PANEL_OPEN = config.getfloat("Sleeps", "SLEEP_PANEL_OPEN")
SLEEP_ACTION     = config.getfloat("Sleeps", "SLEEP_ACTION")

# ---------------- Read Grid Settings ----------------
GRIDS = {
    "main": {
        "start_x": config.getint("Inventory_Grid_Main", "start_x"),
        "start_y": config.getint("Inventory_Grid_Main", "start_y"),
        "cols": config.getint("Inventory_Grid_Main", "cols"),
        "rows": config.getint("Inventory_Grid_Main", "rows")
    },
    "secondary": {
        "start_x": config.getint("Inventory_Grid_Secondary", "start_x"),
        "start_y": config.getint("Inventory_Grid_Secondary", "start_y"),
        "cols": config.getint("Inventory_Grid_Secondary", "cols"),
        "rows": config.getint("Inventory_Grid_Secondary", "rows")
    }
}

# Other constants
CELL_SIZE = 30
BORDER = 1
TOTAL_CELL = CELL_SIZE + (BORDER * 2)

# ================= WIN32 API SETUP =================
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP   = 0x0010
MOUSEEVENTF_LEFTDOWN  = 0x0002
MOUSEEVENTF_LEFTUP    = 0x0004

class Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

def get_cursor_pos():
    pt = Point()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return (pt.x, pt.y)

def set_cursor_pos(x, y):
    ctypes.windll.user32.SetCursorPos(x, y)

# --- Using SendInput for mouse simulation ---
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))
    ]

class _INPUT(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]
    
class INPUT(ctypes.Structure):
    _anonymous_ = ("_input",)
    _fields_ = [("type", ctypes.c_ulong),
                ("_input", _INPUT)]

def send_mouse_input(flags):
    extra = ctypes.c_ulong(0)
    mi = MOUSEINPUT(0, 0, 0, flags, 0, ctypes.pointer(extra))
    inp = INPUT()
    inp.type = 0  # INPUT_MOUSE
    inp.mi = mi
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))

def send_right_click():
    send_mouse_input(MOUSEEVENTF_RIGHTDOWN)
    time.sleep(0.005)
    send_mouse_input(MOUSEEVENTF_RIGHTUP)

def send_left_click():
    """Simulate a left mouse click using SendInput."""
    send_mouse_input(MOUSEEVENTF_LEFTDOWN)
    time.sleep(0.005)
    send_mouse_input(MOUSEEVENTF_LEFTUP)

# ---------------- Global Keyboard ----------------
keyboard = ctypes.WinDLL('User32.dll')

def check_exit():
    if keyboard.GetAsyncKeyState(GLOBAL_EXIT_KEY) & 0x8000:
        print("Global exit key (F10) pressed. Exiting...")
        sys.exit()

def is_grim_dawn_focused():
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
    buff = ctypes.create_unicode_buffer(length + 1)
    ctypes.windll.user32.GetWindowTextW(hwnd, buff, length + 1)
    title = buff.value
    return "Grim Dawn" in title

# ================= CORE FUNCTIONALITY =================
def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception as e:
        if DEBUG:
            print("Admin check error:", e)
        return False

def generate_coordinates(grid):
    coords = []
    start_x = grid['start_x'] + BORDER
    start_y = grid['start_y'] + BORDER
    for row in range(grid['rows']):
        y = start_y + (row * TOTAL_CELL)
        for col in range(grid['cols']):
            x = start_x + (col * TOTAL_CELL)
            coords.append((x, y))
    return coords

def sell_items(coords):
    orig_pos = get_cursor_pos()
    try:
        for idx, (x, y) in enumerate(coords):
            if not is_grim_dawn_focused():
                continue
            if DEBUG:
                print(f"Selling: Clicking cell {idx+1}/{len(coords)} at ({x}, {y})")
            set_cursor_pos(x, y)
            send_right_click()
            check_exit()
            time.sleep(SLEEP_ACTION)
    finally:
        set_cursor_pos(*orig_pos)

def dismantle_items(coords):
    orig_pos = get_cursor_pos()
    try:
        for idx, (x, y) in enumerate(coords):
            if not is_grim_dawn_focused():
                continue
            if DEBUG:
                print(f"Dismantling: Processing cell {idx+1}/{len(coords)} at ({x}, {y})")
            check_exit()
            set_cursor_pos(*TRANSMUTE_TAB_LOCATION)
            send_left_click()
            time.sleep(SLEEP_PANEL_OPEN)
            check_exit()
            set_cursor_pos(*DISMANTLE_TAB_LOCATION)
            send_left_click()
            time.sleep(SLEEP_PANEL_OPEN)
            check_exit()
            set_cursor_pos(x, y)
            send_left_click()
            time.sleep(SLEEP_ACTION)
            check_exit()
            set_cursor_pos(*DISMANTLE_ITEM_LOCATION)
            send_left_click()
            time.sleep(SLEEP_ACTION)
            check_exit()
            set_cursor_pos(*DISMANTLE_BUTTON_LOCATION)
            send_left_click()
            time.sleep(SLEEP_ACTION)
            check_exit()
            set_cursor_pos(*CONFIRM_DISMANTLE_LOCATION)
            send_left_click()
            time.sleep(SLEEP_ACTION)
            check_exit()
            if DEBUG:
                break
    finally:
        set_cursor_pos(*orig_pos)

def sell_all_items():
    coords = generate_coordinates(GRIDS['main']) + generate_coordinates(GRIDS['secondary'])
    sell_items(coords)

def sell_secondary_items():
    coords = generate_coordinates(GRIDS['secondary'])
    sell_items(coords)

def dismantle_all_items():
    coords = generate_coordinates(GRIDS['main']) + generate_coordinates(GRIDS['secondary'])
    dismantle_items(coords)

def dismantle_secondary_items():
    coords = generate_coordinates(GRIDS['secondary'])
    dismantle_items(coords)

def main_loop():
    while True:
        if not is_grim_dawn_focused():
            time.sleep(0.005)
            continue
        if keyboard.GetAsyncKeyState(GLOBAL_EXIT_KEY) & 0x8000:
            print("Global exit key (F10) pressed. Exiting...")
            sys.exit()
        if keyboard.GetAsyncKeyState(SELL_ALL_KEY) & 0x8000:
            sell_all_items()
        elif keyboard.GetAsyncKeyState(SELL_SECONDARY_KEY) & 0x8000:
            sell_secondary_items()
        elif keyboard.GetAsyncKeyState(DISMANTLE_ALL_KEY) & 0x8000:
            dismantle_all_items()
        elif keyboard.GetAsyncKeyState(DISMANTLE_SECONDARY_KEY) & 0x8000:
            dismantle_secondary_items()
        time.sleep(0.005)

def exit_program(icon, item):
    print("Tray exit selected. Exiting...")
    icon.stop()
    sys.exit()

def create_image():
    width = 64
    height = 64
    image = Image.new('RGB', (width, height), color=(0, 0, 0))
    dc = ImageDraw.Draw(image)
    dc.rectangle((0, 0, width, height), fill=(255, 0, 0))
    return image

def setup_tray_icon():
    menu = pystray.Menu(pystray.MenuItem("Exit", exit_program))
    icon = pystray.Icon("AutoSellDismantle", create_image(), "AutoSell & Dismantle", menu)
    icon.run()

def main():
    if not is_admin():
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, " ".join(sys.argv), None, None, 1
        )
        sys.exit()
    print("Auto-sell & Dismantle script running...")
    print("Tray icon active. Use F2-F5 for functions, F10 or tray 'Exit' to quit.")
    print("Macros only work if Grim Dawn is the active window.")
    main_thread = threading.Thread(target=main_loop, daemon=True)
    main_thread.start()
    setup_tray_icon()

if __name__ == "__main__":
    from PIL import Image, ImageDraw
    main()

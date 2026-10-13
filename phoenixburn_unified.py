#!/usr/bin/env python3
"""
PhoenixBurn - ISO to USB Burner
اختر: مبتدئ (مع الشرح) أو محترف (مباشر)
"""

import os
import sys
import pwd
import hashlib
import subprocess
import tempfile
import threading
import urllib.request
import urllib.error
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
from PIL import Image, ImageTk
from io import BytesIO

# =====================================================================
# متغير الوضع (مبتدئ أو محترف)
# =====================================================================
MODE = "beginner"  # سيتم تعيينه من قبل المستخدم

# =====================================================================
# تحميل اللوجو
# =====================================================================
def load_logo_from_file(logo_path):
    """تحميل صورة اللوجو من الملف"""
    try:
        if os.path.isfile(logo_path):
            img = Image.open(logo_path)
            img = img.resize((100, 100), Image.Resampling.LANCZOS)
            return ImageTk.PhotoImage(img)
    except Exception as e:
        print(f"[!] Error loading logo: {e}")
    return None

# =====================================================================
# شخصية سرطان البحر للمبتدئين 🦀
# =====================================================================
class CrabHelper:
    """مساعد سرطان البحر - يشرح كل شيء للمبتدئين"""
    
    @staticmethod
    def greet():
        return """
🦀 مرحباً! أنا سرطان البحر المساعد!
سأساعدك في كل خطوة 🌊

اتبعي التعليمات البسيطة:
1. اختاري ملف ISO
2. اختاري ذاكرة USB
3. اضغطي IGNITE 🔥
"""
    
    @staticmethod
    def explain_iso():
        return """
📀 ملف ISO؟
━━━━━━━━━━━━━━━━━
ملف ISO هو صورة كاملة لنظام التشغيل.
مثلاً: Ubuntu كملف واحد 🐧

نحتاج لحرقه على USB لتثبيت Ubuntu! ✨
"""
    
    @staticmethod
    def explain_usb():
        return """
💾 ذاكرة USB؟
━━━━━━━━━━━━━━━━━
الذاكرة الخارجية التي تضعيها في المنفذ USB.
حجمها يجب أن يكون أكبر من ملف ISO! 📏

تحذير ⚠️: سنمسح كل شيء عليها!
"""
    
    @staticmethod
    def explain_burn():
        return """
🔥 الحرق (Burning)؟
━━━━━━━━━━━━━━━━━━━
ننسخ ملف ISO كاملاً على الذاكرة.
ثم نتحقق من كل بايت! ✓

النتيجة: ذاكرة قابلة للإقلاع! 🚀
"""
    
    @staticmethod
    def explain_verification():
        return """
✅ التحقق (Verification)؟
━━━━━━━━━━━━━━━━━━━━━━━━
نقرأ ما كتبناه على الذاكرة...
ونقارنه مع الأصلي!

لو كل بايت صحيح = نجح! 🎉
لو بايت واحد خاطئ = فشل! ❌
"""

# =====================================================================
# ROOT: writing to a drive needs administrator rights
# =====================================================================
if os.geteuid() != 0:
    print("[!] PhoenixBurn needs administrator rights to write to a drive. Asking sudo...")
    os.execvp("sudo", ["sudo", "-E", sys.executable] + sys.argv)

# =====================================================================
# EMBEDDED C ENGINE
# =====================================================================
C_ENGINE = r"""
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dirent.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <limits.h>
#include <sys/stat.h>
#include <sys/types.h>

#define CHUNK (1024 * 1024)

static void read_sysfs(const char *path, char *buf, size_t len) {
    FILE *f = fopen(path, "r");
    buf[0] = 0;
    if (f) {
        if (fgets(buf, len, f)) buf[strcspn(buf, "\n")] = 0;
        fclose(f);
    }
    if (!buf[0]) snprintf(buf, len, "Unknown");
}

static int is_usb(const char *name) {
    char link[PATH_MAX], real[PATH_MAX];
    snprintf(link, sizeof(link), "/sys/block/%s", name);
    if (!realpath(link, real)) return 0;
    return strstr(real, "/usb") != NULL;
}

static int skip_name(const char *n) {
    return n[0] == '.' || !strncmp(n, "loop", 4) || !strncmp(n, "ram", 3) ||
           !strncmp(n, "dm-", 3) || !strncmp(n, "zram", 4) || !strncmp(n, "sr", 2) ||
           !strncmp(n, "md", 2) || !strncmp(n, "nbd", 3) || !strncmp(n, "fd", 2);
}

static int cmd_scan(void) {
    DIR *dir = opendir("/sys/block");
    if (!dir) { fprintf(stderr, "Cannot read /sys/block\n"); return 1; }
    printf("SCAN_START\n");
    struct dirent *e;
    while ((e = readdir(dir)) != NULL) {
        const char *n = e->d_name;
        if (skip_name(n)) continue;
        char path[PATH_MAX], removable[16], model[256];
        snprintf(path, sizeof(path), "/sys/block/%s/removable", n);
        read_sysfs(path, removable, sizeof(removable));
        if (strcmp(removable, "1") != 0 && !is_usb(n)) continue;
        long long sectors = 0;
        snprintf(path, sizeof(path), "/sys/block/%s/size", n);
        FILE *f = fopen(path, "r");
        if (f) { if (fscanf(f, "%lld", &sectors) != 1) sectors = 0; fclose(f); }
        if (sectors <= 0) continue;
        snprintf(path, sizeof(path), "/sys/block/%s/device/model", n);
        read_sysfs(path, model, sizeof(model));
        for (char *p = model; *p; p++) if (*p == '|') *p = ' ';
        printf("DISK|/dev/%s|%s|%.1f GB|USB\n", n, model,
               (sectors * 512.0) / (1024.0 * 1024.0 * 1024.0));
    }
    printf("SCAN_END\n");
    closedir(dir);
    return 0;
}

static ssize_t read_full(int fd, char *buf, size_t n) {
    size_t got = 0;
    while (got < n) {
        ssize_t r = read(fd, buf + got, n - got);
        if (r < 0) { if (errno == EINTR) continue; return -1; }
        if (r == 0) break;
        got += (size_t)r;
    }
    return (ssize_t)got;
}

static int write_full(int fd, const char *buf, size_t n) {
    size_t put = 0;
    while (put < n) {
        ssize_t w = write(fd, buf + put, n - put);
        if (w < 0) { if (errno == EINTR) continue; return -1; }
        put += (size_t)w;
    }
    return 0;
}

static int cmd_burn(const char *src, const char *dst) {
    int sfd = open(src, O_RDONLY);
    if (sfd < 0) { perror("Cannot open the ISO"); return 1; }
    struct stat st;
    if (fstat(sfd, &st) < 0 || st.st_size <= 0) {
        fprintf(stderr, "The ISO file is empty or unreadable\n"); return 1;
    }
    int dfd = open(dst, O_RDWR | O_SYNC);
    if (dfd < 0) { perror("Cannot open the drive"); return 1; }

#ifndef ALLOW_FILE
    struct stat dst_st;
    if (fstat(dfd, &dst_st) < 0 || !S_ISBLK(dst_st.st_mode)) {
        fprintf(stderr, "%s is not a drive. Refusing to write.\n", dst); return 1;
    }
#endif

    off_t dsize = lseek(dfd, 0, SEEK_END);
    lseek(dfd, 0, SEEK_SET);
    if (dsize < st.st_size) {
        fprintf(stderr, "The ISO (%lld bytes) is bigger than the drive (%lld bytes)\n",
                (long long)st.st_size, (long long)dsize);
        return 1;
    }

    char *a = malloc(CHUNK), *b = malloc(CHUNK);
    if (!a || !b) { fprintf(stderr, "Out of memory\n"); return 1; }

    long long total = st.st_size, done = 0;
    int last = -1;
    printf("START\n"); fflush(stdout);
    ssize_t r;
    while ((r = read_full(sfd, a, CHUNK)) > 0) {
        if (write_full(dfd, a, (size_t)r) < 0) { perror("Write failed"); return 1; }
        done += r;
        int pct = (int)(done * 100 / total);
        if (pct != last) { printf("P:%d\n", pct); fflush(stdout); last = pct; }
    }
    if (r < 0) { perror("Read failed"); return 1; }

    fsync(dfd);
    posix_fadvise(dfd, 0, 0, POSIX_FADV_DONTNEED);

    printf("VERIFY\n"); fflush(stdout);
    lseek(sfd, 0, SEEK_SET);
    lseek(dfd, 0, SEEK_SET);
    done = 0; last = -1;
    while ((r = read_full(sfd, a, CHUNK)) > 0) {
        if (read_full(dfd, b, (size_t)r) != r || memcmp(a, b, (size_t)r) != 0) {
            printf("FAIL\n"); fflush(stdout); return 2;
        }
        done += r;
        int pct = (int)(done * 100 / total);
        if (pct != last) { printf("V:%d\n", pct); fflush(stdout); last = pct; }
    }
    printf("OK\n"); fflush(stdout);
    close(sfd); close(dfd);
    free(a); free(b);
    return 0;
}

int main(int argc, char *argv[]) {
    if (argc < 2) { fprintf(stderr, "Usage: engine [scan | burn <iso> <drive>]\n"); return 1; }
    if (!strcmp(argv[1], "scan")) return cmd_scan();
    if (!strcmp(argv[1], "burn") && argc >= 4) return cmd_burn(argv[2], argv[3]);
    fprintf(stderr, "Unknown command\n"); return 1;
}
"""

# =====================================================================
# الألوان والأنماط
# =====================================================================
INK = "#1a1a1a"
PANEL = "#2a2a2a"
EMBER = "#3a2530"
TEXT = "#e8e8e8"
MUTED = "#888888"
GOLD = "#ffb84d"
OK_GREEN = "#7fbf7f"
FAIL_RED = "#bf7f7f"
CRIMSON = "#c73e1d"
ORANGE = "#e85c2e"

# =====================================================================
# مساعدات النظام
# =====================================================================
def real_user_home():
    return pwd.getpwuid(int(os.environ.get("SUDO_UID", 0))).pw_dir if os.environ.get("SUDO_UID") else os.path.expanduser("~")

def get_engine():
    engine_path = os.path.join(tempfile.gettempdir(), "phoenixburn_engine")
    if not os.path.exists(engine_path):
        src_file = os.path.join(tempfile.gettempdir(), "phoenix.c")
        with open(src_file, "w") as f:
            f.write(C_ENGINE)
        os.system(f"gcc -O2 -o {engine_path} {src_file}")
        os.remove(src_file)
    return engine_path

def run_engine(args):
    proc = subprocess.run([get_engine()] + args, capture_output=True, text=True)
    return proc.stdout.strip().split("\n"), proc.stderr.strip()

def root_disk_name():
    result = subprocess.run(["lsblk", "-nd", "-o", "NAME", "/"], capture_output=True, text=True)
    return result.stdout.strip().split("\n")[0] if result.stdout else "sda"

def unmount_all(device):
    errors = []
    partitions = subprocess.run(["lsblk", "-ln", "-o", "NAME", device], capture_output=True, text=True)
    for part in partitions.stdout.strip().split("\n"):
        if part:
            ret = os.system(f"umount /dev/{part} 2>/dev/null")
            if ret != 0:
                errors.append(f"Could not unmount /dev/{part}")
    return errors

# =====================================================================
# اختيار الوضع (مبتدئ أو محترف)
# =====================================================================
def choose_mode():
    """نافذة اختيار الوضع"""
    global MODE
    
    root = tk.Tk()
    root.title("PhoenixBurn - Choose Mode")
    root.geometry("500x300")
    root.configure(bg=INK)
    
    # العنوان
    frame = tk.Frame(root, bg=INK)
    frame.pack(fill="both", expand=True, padx=20, pady=20)
    
    tk.Label(frame, text="🔥 PhoenixBurn", font=("Arial", 24, "bold"), 
             bg=INK, fg=CRIMSON).pack(pady=(0, 10))
    
    tk.Label(frame, text="Choose Your Mode:", font=("Arial", 14), 
             bg=INK, fg=TEXT).pack(pady=(0, 30))
    
    # الزر 1: مبتدئ
    def set_beginner():
        global MODE
        MODE = "beginner"
        root.destroy()
    
    tk.Button(frame, text="🦀 Beginner Mode\n(مع شرح مفصل)", 
              command=set_beginner, bg=GOLD, fg="black",
              relief="flat", padx=20, pady=15, font=("Arial", 12, "bold"),
              activebackground=ORANGE).pack(fill="x", pady=10)
    
    # الزر 2: محترف
    def set_professional():
        global MODE
        MODE = "professional"
        root.destroy()
    
    tk.Button(frame, text="⚡ Professional Mode\n(مباشر بدون شرح)", 
              command=set_professional, bg=CRIMSON, fg="white",
              relief="flat", padx=20, pady=15, font=("Arial", 12, "bold"),
              activebackground=ORANGE).pack(fill="x", pady=10)
    
    root.mainloop()

# =====================================================================
# GUI الرئيسية
# =====================================================================
class FlameGauge(tk.Canvas):
    def __init__(self, parent):
        super().__init__(parent, width=480, height=60, bg=INK, highlightthickness=0)
        self.pack(pady=(0, 10))
        self.progress = 0
        self.label = "waiting"

    def set(self, pct, label):
        self.progress = pct
        self.label = label
        self.redraw()

    def redraw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        self.create_rectangle(0, 0, w, h, fill=PANEL, outline=MUTED)
        bar_w = int(w * self.progress / 100)
        self.create_rectangle(0, 0, bar_w, h, fill=CRIMSON, outline="")
        self.create_text(w/2, h/2, text=f"{self.label} - {self.progress}%", fill=TEXT, font=("Consolas", 10))

class Panel:
    def __init__(self, parent, title):
        self.frame = tk.Frame(parent, bg=INK, relief="raised", bd=1)
        self.frame.pack(fill="both", expand=True, pady=(0, 10))
        tk.Label(self.frame, text=title, bg=INK, fg=GOLD, font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=10, pady=(10, 0))
        self.body = tk.Frame(self.frame, bg=INK)
        self.body.pack(fill="both", expand=True, padx=10, pady=10)

class PhoenixBurnGUI:
    def __init__(self, root, logo_path=None):
        self.root = root
        self.root.title("🔥 PhoenixBurn - ISO to USB")
        self.root.geometry("600x750")
        self.root.configure(bg=INK)

        self.iso_path = None
        self.drives_data = []
        self.selected_drive = tk.IntVar(value=-1)

        self._build_header(logo_path)
        self._build_fuel()
        self._build_vessel()
        self._build_ignite()
        self._setup_navigation()

        self.fuel_lbl.config(text="fuel: (none)", fg=MUTED)
        self._refresh_ignite()
        
        # إظهار الرسالة الترحيبية للمبتدئين
        if MODE == "beginner":
            messagebox.showinfo("مرحباً! 🦀", CrabHelper.greet())

    def _build_header(self, logo_path):
        """بناء رأس النافذة مع اللوجو"""
        header = tk.Frame(self.root, bg=INK)
        header.pack(fill="x", padx=20, pady=20)

        if logo_path and os.path.isfile(logo_path):
            logo_img = load_logo_from_file(logo_path)
            if logo_img:
                logo_label = tk.Label(header, image=logo_img, bg=INK)
                logo_label.image = logo_img
                logo_label.pack(side="left", padx=(0, 20))

        title_frame = tk.Frame(header, bg=INK)
        title_frame.pack(side="left", fill="both", expand=True)
        tk.Label(title_frame, text="PhoenixBurn", font=("Segoe UI", 24, "bold"), bg=INK, fg=CRIMSON).pack(anchor="w")
        mode_text = "Beginner 🦀" if MODE == "beginner" else "Professional ⚡"
        tk.Label(title_frame, text=f"ISO to USB - {mode_text}", font=("Segoe UI", 10), bg=INK, fg=MUTED).pack(anchor="w")

    def _build_fuel(self):
        """قسم اختيار ISO"""
        self.panel_fuel = Panel(self.root, "FUEL (ISO)")
        
        if MODE == "beginner":
            tk.Button(self.panel_fuel.body, text="ℹ️ ما هو ISO؟", command=lambda: messagebox.showinfo("شرح ISO", CrabHelper.explain_iso()),
                     bg=PANEL, fg=GOLD, relief="flat", padx=10, pady=4, font=("Arial", 8)).pack(anchor="e", padx=5, pady=(0, 5))
        
        self.fuel_lbl = tk.Label(self.panel_fuel.body, text="fuel: (none)", bg=INK, fg=TEXT, font=("Segoe UI", 10))
        self.fuel_lbl.pack(anchor="w")
        tk.Button(self.panel_fuel.body, text="fetch fuel", command=self._build_fuel_menu, bg=PANEL, fg=GOLD, relief="flat", padx=10, pady=6).pack(anchor="w")

    def _build_fuel_menu(self):
        menu = tk.Menu(self.root, tearoff=False, bg=PANEL, fg=TEXT)
        menu.add_command(label="a file already on this computer", command=lambda: self._choose_fuel("a file already on this computer"))
        menu.post(self.root.winfo_pointerx(), self.root.winfo_pointery())

    def _choose_fuel(self, name):
        f = filedialog.askopenfilename(filetypes=[("ISO Files", "*.iso"), ("All Files", "*.*")])
        if f:
            self.iso_path = f
            self.fuel_lbl.config(text=f"fuel: {os.path.basename(f)}", fg=OK_GREEN)

    def _build_vessel(self):
        self.panel_vessel = Panel(self.root, "VESSEL (USB)")
        
        if MODE == "beginner":
            tk.Button(self.panel_vessel.body, text="ℹ️ ما هي الذاكرة؟", command=lambda: messagebox.showinfo("شرح USB", CrabHelper.explain_usb()),
                     bg=PANEL, fg=GOLD, relief="flat", padx=10, pady=4, font=("Arial", 8)).pack(anchor="e", padx=5, pady=(0, 5))
        
        tk.Button(self.panel_vessel.body, text="look for a vessel", command=self.scan_disks, bg=PANEL, fg=GOLD, relief="flat", padx=10, pady=6).pack(anchor="w")
        self.drive_list = tk.Frame(self.panel_vessel.body, bg=INK)
        self.drive_list.pack(fill="both", expand=True, pady=(12, 0))
        self.selected_drive = tk.IntVar(value=-1)

    def scan_disks(self):
        self.set_status("looking for USB drives...", GOLD)
        self.root.update_idletasks()
        lines, err = run_engine(["scan"])
        for w in self.drive_list.winfo_children():
            w.destroy()
        if err:
            self.set_status(f"scan error: {err}", FAIL_RED)
            return
        skip = root_disk_name()
        self.drives_data = []
        for line in lines:
            if not line.startswith("DISK|"):
                continue
            parts = line.split("|")
            if len(parts) == 5 and os.path.basename(parts[1]) != skip:
                self.drives_data.append({"path": parts[1], "model": parts[2].strip(), "size": parts[3]})
        if not self.drives_data:
            tk.Label(self.drive_list, text="no vessel found - plug one in and look again", bg=INK, fg=MUTED).pack(anchor="w")
        for i, d in enumerate(self.drives_data):
            row = tk.Frame(self.drive_list, bg=EMBER, highlightbackground="#3a2530", highlightthickness=1)
            row.pack(fill="x", pady=4)
            tk.Radiobutton(row, text=f"{d['path']}   {d['model']}   ({d['size']})", variable=self.selected_drive, value=i, bg=EMBER, fg=TEXT, selectcolor=PANEL, activebackground=EMBER, activeforeground=TEXT, font=("Segoe UI", 10), anchor="w", padx=8, pady=10).pack(fill="x")
        if self.drives_data:
            self.selected_drive.set(0)
        self.set_status("", MUTED)

    def _build_ignite(self):
        self.panel_ignite = Panel(self.root, "IGNITE")
        
        if MODE == "beginner":
            tk.Button(self.panel_ignite.body, text="ℹ️ ما هو الحرق؟", command=lambda: messagebox.showinfo("شرح الحرق", CrabHelper.explain_burn()),
                     bg=PANEL, fg=GOLD, relief="flat", padx=10, pady=4, font=("Arial", 8)).pack(anchor="e", padx=5, pady=(0, 5))
        
        self.summary_lbl = tk.Label(self.panel_ignite.body, text="", bg=INK, fg=TEXT, font=("Consolas", 10), justify="left", wraplength=460)
        self.summary_lbl.pack(anchor="w", pady=(0, 10))
        self.gauge = FlameGauge(self.panel_ignite.body)
        tk.Label(self.panel_ignite.body, text="type the vessel's path to confirm:", bg=INK, fg=MUTED).pack(anchor="w")
        self.confirm_entry = tk.Entry(self.panel_ignite.body, bg=PANEL, fg=TEXT, insertbackground=TEXT, relief="flat", font=("Consolas", 12))
        self.confirm_entry.pack(fill="x", pady=(4, 12), ipady=6)
        self.burn_btn = tk.Button(self.panel_ignite.body, text="IGNITE", command=self.start_burn, bg=CRIMSON, fg="white", relief="flat", font=("Segoe UI", 12, "bold"), pady=10, activebackground=ORANGE, activeforeground="white")
        self.burn_btn.pack(fill="x")

    def _refresh_ignite(self):
        iso = os.path.basename(self.iso_path) if self.iso_path else "(none)"
        if self.drives_data and 0 <= self.selected_drive.get() < len(self.drives_data):
            d = self.drives_data[self.selected_drive.get()]
            drive_txt = f"{d['path']}  {d['model']} ({d['size']})"
        else:
            drive_txt = "(none)"
        self.summary_lbl.config(text=f"fuel    {iso}\nvessel  {drive_txt}\n\neverything on the vessel will be replaced.")
        self.gauge.set(0, "waiting")

    def _setup_navigation(self):
        nav = tk.Frame(self.root, bg=INK)
        nav.pack(fill="x", padx=20, pady=(0, 20))
        self.back_btn = tk.Button(nav, text="back", state="disabled", bg=PANEL, fg=MUTED, relief="flat", padx=10)
        self.back_btn.pack(side="left", padx=(0, 5))
        self.next_btn = tk.Button(nav, text="next", state="disabled", bg=PANEL, fg=MUTED, relief="flat", padx=10)
        self.next_btn.pack(side="left")
        self.status_lbl = tk.Label(self.root, text="ready", bg=INK, fg=MUTED, font=("Consolas", 9))
        self.status_lbl.pack(fill="x", padx=20, pady=(0, 20))

    def set_status(self, msg, color):
        self.status_lbl.config(text=msg, fg=color)

    def ui(self, fn, *args, **kwargs):
        self.root.after(0, lambda: fn(*args, **kwargs))

    def start_burn(self):
        if not self.iso_path or not os.path.isfile(self.iso_path):
            messagebox.showerror("No fuel", "Choose or fetch a valid ISO first.")
            return
        if not self.drives_data or self.selected_drive.get() < 0:
            messagebox.showerror("No vessel", "Choose a USB drive first.")
            return
        dev = self.drives_data[self.selected_drive.get()]
        if self.confirm_entry.get().strip() != dev["path"]:
            messagebox.showwarning("Not confirmed", f"Type exactly {dev['path']} to confirm.")
            return

        errors = unmount_all(dev["path"])
        if errors:
            messagebox.showerror("Cannot unmount", "Close any window using the drive:\n\n" + "\n".join(errors))
            return

        self.burn_btn.config(state=tk.DISABLED)
        self.next_btn.config(state=tk.DISABLED)
        self.back_btn.config(state=tk.DISABLED)
        self.gauge.set(0, "writing")

        def worker():
            result = None
            try:
                proc = subprocess.Popen([get_engine(), "burn", self.iso_path, dev["path"]], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                for line in proc.stdout:
                    line = line.strip()
                    if line.startswith("P:"):
                        p = int(line[2:])
                        self.ui(self.gauge.set, p, "writing")
                    elif line == "VERIFY":
                        self.ui(self.gauge.set, 0, "verifying")
                    elif line.startswith("V:"):
                        p = int(line[2:])
                        self.ui(self.gauge.set, p, "verifying")
                    elif line == "OK":
                        result = (True, "done - every byte was verified.")
                    elif line == "FAIL":
                        result = (False, "verification failed - the drive may be bad. Try again.")
                proc.wait()
                if result is None:
                    result = (False, proc.stderr.read().strip() or "the engine stopped unexpectedly.")
            except Exception as e:
                result = (False, str(e))
            self.ui(self.finish, *result)
        threading.Thread(target=worker, daemon=True).start()

    def finish(self, ok, msg):
        self.burn_btn.config(state=tk.NORMAL)
        self.next_btn.config(state=tk.NORMAL)
        self.back_btn.config(state=tk.NORMAL)
        if ok:
            self.gauge.set(100, "complete")
            if MODE == "beginner":
                messagebox.showinfo("نجح! 🎉", CrabHelper.explain_verification() + f"\n\n{msg}")
        self.set_status(msg, OK_GREEN if ok else FAIL_RED)
        (messagebox.showinfo if ok else messagebox.showerror)("Done" if ok else "Error", msg)

if __name__ == "__main__":
    # اختيار الوضع
    choose_mode()
    
    # تحديد مسار اللوجو
    logo_path = "phoenixburn-logo.png"
    
    root = tk.Tk()
    app = PhoenixBurnGUI(root, logo_path)
    root.mainloop()

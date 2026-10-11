<div align="center">

<img src="phoenixburn-logo.png" width="120" alt="PhoenixBurn logo"/>

# 🔥 PhoenixBurn

### A real ISO → USB burning tool, built with Python + C

**Part of the ALphoenixa OS / dstc project**

![Platform](https://img.shields.io/badge/platform-Linux-orange?style=flat-square)
![Language](https://img.shields.io/badge/GUI-Python%20%2F%20tkinter-blue?style=flat-square)
![Language](https://img.shields.io/badge/Core-C-lightgrey?style=flat-square)
![License](https://img.shields.io/badge/license-Open%20Source-green?style=flat-square)
![Status](https://img.shields.io/badge/status-Active-success?style=flat-square)

</div>

---

## 🖼️ Preview

A Rufus-style interface for writing OS images to USB drives — clean, familiar, and to the point.

```
Device Options → Format Options → Status → START
```

---

## ⚡ What is PhoenixBurn?

**PhoenixBurn** is a real, working ISO-to-USB burner — not a simulation. It lets you flash an operating system image onto a USB drive, the same way tools like Rufus or balenaEtcher do.

It's built with a hybrid approach:

- 🐍 **Python (tkinter)** — the graphical interface (device list, ISO picker, format options, live progress)
- ⚙️ **C** — the actual low-level disk-writing engine (`burner_helper.c`), compiled as a native binary for fast, direct block I/O

> Python handles the experience. C handles the bytes.

---

## ✨ Features

- 🎯 Familiar Rufus-style layout — Device Options, Format Options, Status
- 🔍 **Removable-drive-only detection** — your internal disk is never even listed
- 🛡️ Multi-layer safety: protected mount-point checks + type-to-confirm device path
- 📊 Real-time progress bar powered by the C engine
- 🗂️ ISO file picker with auto-suggested volume label
- 🧩 Partition scheme / target system / file system / cluster size options
- ⚡ Native C write engine — 4MB block writes, synced to disk

---

## 🚀 Quick Start

```bash
# 1. Install requirements
sudo apt install python3-tk gcc

# 2. Compile the C burning engine
gcc -O2 -o burner_helper burner_helper.c

# 3. Run the GUI (root is required to write to a raw device)
sudo python3 rufus_dstc_gui2.py
```

---

## ⚠️ Important Safety Notice

> **This tool writes real data to real USB drives.**
> Selecting a drive and confirming will **permanently erase everything on it.** There is no undo.

Built-in protections:
- Only **removable** drives are shown — internal drives are filtered out entirely
- Any drive containing `/`, `/boot`, `/boot/efi`, `/home`, or active swap is automatically excluded
- You must **type the exact device path** to confirm before anything is written

Always double-check the selected device before clicking **START**.

---

## 🧠 Why Python + C?

| Layer | Language | Why |
|---|---|---|
| Interface | Python (tkinter) | Fast to build, easy to read, no external GUI dependencies |
| Disk I/O | C | Direct, low-level, and fast — the same approach real burning tools use under the hood |

The GUI never touches the disk directly — it hands off to the compiled `burner_helper` binary and streams live progress back.

---

## 🔥 Part of a Bigger Project

PhoenixBurn is one piece of the **ALphoenixa OS / dstc** ecosystem — a set of educational, simulation-first projects exploring how operating systems, terminals, and system tools work under the hood.

---

<div align="center">

**Built by Adam** 🔥

*If this project taught you something, consider starring it ⭐*

</div>

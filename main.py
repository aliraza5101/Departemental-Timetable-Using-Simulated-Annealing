import tkinter as tk
from tkinter import messagebox, scrolledtext
from tkinter import filedialog
import random, math, time
from datetime import datetime
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.lib.pagesizes import legal
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
import matplotlib.pyplot as plt

# ----------------------------
# Globals
# ----------------------------
courses, faculty, rooms, slots, preferred_slots = [], {}, [], [], {}
requirements = {}

# Caches for efficiency
_slot_parsed = {}          # slot_str -> (day, start_minutes)
_course_teacher_cache = {} # course -> teacher (cached for speed)

# ----------------------------
# Tunable weights
# ----------------------------
WEIGHTS = {
    'teacher_slot_conflict': 20000,
    'room_slot_conflict': 20000,
    'not_preferred': 2,
    'back_to_back': 1,
    'gap': 1,
    'too_many_sessions_day': 10,
    'missing_teacher': 20000,
    'malformed_slot': 20000
}

# ----------------------------
# Helper / Cache functions
# ----------------------------
def parse_slot(slot):
    """Parse a single slot string and cache the result. Returns (day, start_minutes) or raises."""
    if slot in _slot_parsed:
        return _slot_parsed[slot]
    # expected "Mon 09:00-11:00"
    day, time_range = slot.split(" ", 1)
    start_str = time_range.split("-")[0]
    hh, mm = map(int, start_str.split(":"))
    start_minutes = hh * 60 + mm
    _slot_parsed[slot] = (day, start_minutes)
    return _slot_parsed[slot]

def refresh_caches():
    """Call when slots/faculty/courses change to refresh internal caches."""
    _slot_parsed.clear()
    # prime slot cache with existing slots (avoid repeated parsing cost)
    for s in slots:
        try:
            parse_slot(s)
        except Exception:
            # ignore malformed slots for now; cost function will penalize
            pass
    _course_teacher_cache.clear()
    for c in courses:
        _course_teacher_cache[c] = faculty.get(c, "")

# ----------------------------
# Cost Function (optimized)
# ----------------------------
def cost_function(timetable):
    """
    Optimized cost: uses local variables, cached parsed slots and teacher lookups
    to minimize repeated string parsing and dictionary lookups inside loops.
    """
    penalty = 0
    used_faculty = set()   # (teacher, slot)
    used_rooms = set()     # (room, slot)
    faculty_schedule = {}  # teacher -> day -> list of start minutes

    # localize frequently used globals for speed
    local_pref = preferred_slots
    local_weights = WEIGHTS
    local_course_teacher = _course_teacher_cache

    for (course, slot, room) in timetable:
        teacher = local_course_teacher.get(course, "")
        if not teacher:
            penalty += local_weights['missing_teacher']
            continue

        key_fac = (teacher, slot)
        if key_fac in used_faculty:
            penalty += local_weights['teacher_slot_conflict']
        else:
            used_faculty.add(key_fac)

        key_room = (room, slot)
        if key_room in used_rooms:
            penalty += local_weights['room_slot_conflict']
        else:
            used_rooms.add(key_room)

        # preferred slot check (fast membership checks)
        if teacher in local_pref and local_pref[teacher]:
            if slot not in local_pref[teacher]:
                penalty += local_weights['not_preferred']

        # parse slot using cached parse_slot (fast)
        try:
            day, start_minutes = parse_slot(slot)
        except Exception:
            penalty += local_weights['malformed_slot']
            continue

        # accumulate teacher day schedule
        faculty_schedule.setdefault(teacher, {}).setdefault(day, []).append(start_minutes)

    # day-level penalties (iterate over schedules)
    for teacher, days in faculty_schedule.items():
        for day, starts in days.items():
            starts.sort()
            n = len(starts)
            if n >= 5:  # too many sessions/day
                penalty += local_weights['too_many_sessions_day'] * (n - 4)
            # consecutive pairs
            for i in range(1, n):
                diff = starts[i] - starts[i - 1]
                if 0 < diff <= 60:
                    penalty += local_weights['back_to_back']
                elif diff > 60:
                    penalty += local_weights['gap']
    return penalty

# ----------------------------
# SA neighbor / init (small improvements)
# ----------------------------
def random_solution():
    """Generate a random initial timetable with bias toward preferred slots.
       Use local references to avoid repeated global lookups."""
    timetable = []
    local_slots = slots
    local_rooms = rooms
    local_pref = preferred_slots
    local_faculty = faculty

    for course in courses:
        count = requirements.get(course, 1)
        for _ in range(count):
            teacher = local_faculty.get(course, "")
            if teacher in local_pref and local_pref[teacher]:
                slot = random.choice(local_pref[teacher]) if random.random() < 0.8 else random.choice(local_slots)
            else:
                slot = random.choice(local_slots)
            room = random.choice(local_rooms)
            timetable.append((course, slot, room))
    return timetable

def neighbor_solution(timetable):
    """Create a neighbor timetable by swapping or mutating assignments.
       Works in-place on a shallow copy to reduce allocation cost."""
    if not timetable:
        return []
    new_tt = list(timetable)  # shallow copy
    move = random.random()
    L = len(new_tt)
    if move < 0.45 and L >= 2:
        i, j = random.sample(range(L), 2)
        c1, s1, r1 = new_tt[i]
        c2, s2, r2 = new_tt[j]
        if random.random() < 0.7:
            # swap slots only
            new_tt[i] = (c1, s2, r1)
            new_tt[j] = (c2, s1, r2)
        else:
            # swap entire assignments
            new_tt[i], new_tt[j] = new_tt[j], new_tt[i]
    elif move < 0.85:
        idx = random.randrange(L)
        c, _, r = new_tt[idx]
        teacher = faculty.get(c, "")
        if teacher in preferred_slots and preferred_slots[teacher] and random.random() < 0.7:
            new_slot = random.choice(preferred_slots[teacher])
        else:
            new_slot = random.choice(slots)
        if random.random() < 0.25:
            new_tt[idx] = (c, new_slot, r)
        else:
            new_tt[idx] = (c, new_slot, random.choice(rooms))
    else:
        idx = random.randrange(L)
        c, s, _ = new_tt[idx]
        new_tt[idx] = (c, s, random.choice(rooms))
    return new_tt

# ----------------------------
# Simulated Annealing (kept behavior; micro-optimizations)
# ----------------------------
def simulated_annealing(max_iter=120000, T0=500.0, alpha=0.9997, stop_if_zero=True, live_plot=False, update_interval=None):
    start_time = time.perf_counter()
    current = random_solution()
    current_cost = cost_function(current)
    best, best_cost = current[:], current_cost

    T = T0
    history = {'iter': [], 'current_cost': [], 'best_cost': []}

    # Setup live plot if requested (reduce redraws using update_interval)
    if live_plot:
        plt.ion()
        fig, ax = plt.subplots(figsize=(9, 4))
        line_current, = ax.plot([], [], label='Current Cost', linewidth=1)
        line_best, = ax.plot([], [], label='Best Cost', linewidth=2)
        ax.set_xlabel('Iteration')
        ax.set_ylabel('Cost')
        ax.set_title('Simulated Annealing Progress (Live)')
        ax.grid(True, linestyle='--', alpha=0.4)
        ax.legend(loc='upper right', frameon=True)
        text_box = ax.text(0.02, 0.95, "", transform=ax.transAxes, va='top', fontsize=9,
                           bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

        if update_interval is None:
            update_interval = max(1, int(max_iter / 300))

        plot_iters = []
        plot_current = []
        plot_best = []

    # main loop
    for it in range(1, max_iter + 1):
        if T <= 1e-8:
            break
        neighbor = neighbor_solution(current)
        neighbor_cost = cost_function(neighbor)
        delta = neighbor_cost - current_cost

        # acceptance
        if delta < 0 or random.random() < math.exp(-delta / max(T, 1e-9)):
            current, current_cost = neighbor, neighbor_cost
            if current_cost < best_cost:
                best, best_cost = current[:], current_cost

        history['iter'].append(it)
        history['current_cost'].append(current_cost)
        history['best_cost'].append(best_cost)

        if live_plot and (it % update_interval == 0 or it == 1):
            plot_iters.append(it)
            plot_current.append(current_cost)
            plot_best.append(best_cost)

            line_current.set_xdata(plot_iters)
            line_current.set_ydata(plot_current)
            line_best.set_xdata(plot_iters)
            line_best.set_ydata(plot_best)

            # autoscale only on data update (fewer calls)
            ax.relim()
            ax.autoscale_view()

            text_box.set_text(f"Iter: {it}\nCurrent: {current_cost}\nBest: {best_cost}\nT: {T:.4f}")
            plt.draw()
            plt.pause(0.001)

        if stop_if_zero and best_cost == 0:
            break

        T *= alpha

    elapsed = time.perf_counter() - start_time

    if live_plot:
        # final plot update
        plot_iters.append(it)
        plot_current.append(current_cost)
        plot_best.append(best_cost)

        line_current.set_xdata(plot_iters)
        line_current.set_ydata(plot_current)
        line_best.set_xdata(plot_iters)
        line_best.set_ydata(plot_best)
        ax.relim()
        ax.autoscale_view()
        text_box.set_text(f"Done\nIter: {it}\nCurrent: {current_cost}\nBest: {best_cost}\nElapsed: {elapsed:.2f}s")
        plt.draw()
        plt.ioff()
        plt.show()

    return best, best_cost, history, elapsed

# ----------------------------
# Analyzer Results (unchanged behavior, small cache usage)
# ----------------------------
def analyze_results(best_tt, init_cost):
    best_cost = cost_function(best_tt)

    pref_hits = 0
    total_with_prefs = 0
    for course, slot, room in best_tt:
        teacher = faculty.get(course, "")
        if teacher in preferred_slots:
            total_with_prefs += 1
            if slot in preferred_slots[teacher]:
                pref_hits += 1

    summary = (
        f"--- Timetable Analysis ---\n"
        f"Initial (random) cost ≈ {init_cost}\n"
        f"Best cost after SA = {best_cost}\n"
        f"Preferred-slot matches: {pref_hits}/{total_with_prefs}\n"
        f"(0 means no teacher/room clashes because penalty handle ho gyi)\n"
        f"Back-to-back/gap penalties bhi is cost mein include hain.\n"
    )
    return summary

# ----------------------------
# Save PDF (unchanged except minor safety)
# ----------------------------
def save_pdf(timetable, filename="Lab_timetable.pdf"):
    doc = SimpleDocTemplate(filename, pagesize=legal, rightMargin=20, leftMargin=20, topMargin=30, bottomMargin=20)
    styles = getSampleStyleSheet()
    cell_style = ParagraphStyle('cell', parent=styles['Normal'], fontSize=9, alignment=1)
    header_style = ParagraphStyle('header', parent=styles['Normal'], fontSize=10, textColor=colors.white, alignment=1, fontName="Helvetica-Bold")

    data = [[Paragraph("Course", header_style), Paragraph("Teacher", header_style),
             Paragraph("Room", header_style), Paragraph("Slot", header_style)]]

    for course, slot, room in timetable:
        data.append([Paragraph(course, cell_style), Paragraph(faculty.get(course, ""), cell_style),
                     Paragraph(room, cell_style), Paragraph(slot, cell_style)])

    table = Table(data, colWidths=[200, 120, 80, 120])
    style = TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#800000")),
        ('TEXTCOLOR',(0,0),(-1,0),colors.white),
        ('GRID',(0,0),(-1,-1),0.5,colors.black),
        ('ALIGN',(0,0),(-1,-1),'CENTER')
    ])

    for row in range(1, len(data)):
        bg = colors.HexColor("#FDEDEC") if row % 2 == 0 else colors.HexColor("#FDF2F2")
        style.add('BACKGROUND', (0,row), (-1,row), bg)

    table.setStyle(style)
    elements = [
        Paragraph("<b><font color='#800000'>Lab TimeTable By Ali Raza (Improved)</font></b>", styles['Title']),
        Spacer(1,12), table,
        Spacer(1,12),
        Paragraph(f"Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", styles['Normal'])
    ]
    doc.build(elements)
    try:
        messagebox.showinfo("Success", f"Timetable saved as {filename}")
    except Exception:
        # in headless or non-standard environments, messagebox may fail
        pass

# ----------------------------
# Plotting function (static save)
# ----------------------------
def plot_history(history, image_path='sa_progress.png'):
    if not history or not history['iter']:
        return None

    iters = history['iter']
    current = history['current_cost']
    best = history['best_cost']

    plt.figure(figsize=(8, 4))
    plt.plot(iters, current, label='Current Cost', linewidth=1)
    plt.plot(iters, best, label='Best Cost', linewidth=2)
    plt.axhline(y=0, linestyle='--', linewidth=1, label='Zero Cost (Perfect)')

    plt.xlabel('Iterations')
    plt.ylabel('Cost Value')
    plt.title(f"Simulated Annealing Progress\nFinal Best Cost = {best[-1]} after {len(iters)} iterations")
    plt.legend(loc='upper right', frameon=True, shadow=True)
    plt.grid(True, linestyle='--', alpha=0.5)
    plt.tight_layout()
    plt.savefig(image_path, dpi=200)
    plt.close()
    return image_path

# ----------------------------
# Load Inputs from File (kept, but refresh caches after load)
# ----------------------------
def load_inputs_from_file():
    file_path = filedialog.askopenfilename(
        title="Select Input File",
        filetypes=[("Text Files", "*.txt"), ("Python Files", "*.py")]
    )
    if not file_path:
        return

    try:
        with open(file_path, "r") as f:
            content = f.read()

        env = {}
        exec(content, {}, env)

        courses.clear(); courses.extend(env.get("courses", []))
        faculty.clear(); faculty.update(env.get("faculty", {}))
        rooms.clear(); rooms.extend(env.get("rooms", []))
        slots.clear(); slots.extend(env.get("slots", []))
        preferred_slots.clear(); preferred_slots.update(env.get("preferred_slots", {}))

        # refresh caches after loading new inputs
        refresh_caches()

        messagebox.showinfo("Success", f"✅ Inputs loaded from {file_path}")
    except Exception as e:
        messagebox.showerror("Error", f"Failed to load file:\n{e}")

# ----------------------------
# Generate Timetable (kept behavior)
# ----------------------------
def generate_timetable():
    global requirements
    if not courses or not faculty or not rooms or not slots:
        messagebox.showerror("Error", "Please enter all inputs first!")
        return

    # default sessions per course (kept as you had it)
    requirements = {c: 2 for c in courses}

    # refresh caches to ensure slot parsing and teacher lookup are ready
    refresh_caches()

    # initial random cost for reference
    init_sol = random_solution()
    init_cost = cost_function(init_sol)

    # SA settings (same defaults)
    max_iter = 120000
    T0 = 500.0
    alpha = 0.9997

    if max_iter > 50000:
        update_interval = 50
    elif max_iter > 20000:
        update_interval = 20
    else:
        update_interval = 5

    best_tt, best_cost, history, elapsed = simulated_annealing(
        max_iter=max_iter, T0=T0, alpha=alpha, stop_if_zero=True, live_plot=True, update_interval=update_interval
    )

    preview_box.delete("1.0", tk.END)
    for course, slot, room in best_tt:
        preview_box.insert(tk.END, f"{course:55} | {faculty.get(course):20} | {room:10} | {slot}\n")

    # Analysis section
    pref_hits, total_with_prefs = 0, 0
    for course, slot, room in best_tt:
        teacher = faculty.get(course, "")
        if teacher in preferred_slots:
            total_with_prefs += 1
            if slot in preferred_slots[teacher]:
                pref_hits += 1

    analysis = (
        "\n--- Timetable Analysis ---\n"
        f"Initial (random) cost ≈ {init_cost}\n"
        f"Best cost after SA = {best_cost}\n"
        f"Preferred-slot matches: {pref_hits}/{total_with_prefs}\n"
        f"Elapsed time: {elapsed:.2f} seconds\n"
    )
    preview_box.insert(tk.END, "\n" + analysis)

    image_path = plot_history(history)
    if image_path:
        preview_box.insert(tk.END, f"\nSA progress plot saved to: {image_path}\n")

    save_pdf(best_tt)

# ----------------------------
# GUI (single Tk instance only)
# ----------------------------
root = tk.Tk()
root.title("AI Timetable Generator by Ali Raza")
root.configure(bg="#1C1C1C")
root.geometry("980x690")

tk.Label(root, text="Lab Timetable Generator Using Simulated Annealing by Ali Raza",
         font=("Arial", 18, "bold"), fg="white", bg="#800000", pady=14).pack(fill="x")

main_frame = tk.Frame(root, bg="#1C1C1C")
main_frame.pack(fill="both", expand=True, padx=20, pady=20)

button_frame = tk.Frame(main_frame, bg="#1C1C1C")
button_frame.pack(side="left", padx=25, pady=30, fill="y")

def show_input_window(title, var):
    def save_data():
        try:
            val = eval(input_box.get("1.0", tk.END).strip())
            var.clear()
            if isinstance(var, list):
                var.extend(val)
            elif isinstance(var, dict):
                var.update(val)

            # refresh caches whenever user changes slots/faculty/courses
            if title in ("Slots", "Faculty", "Courses"):
                refresh_caches()

            messagebox.showinfo("Saved", f"{title} saved successfully!")
            win.destroy()
        except Exception as e:
            messagebox.showerror("Error", str(e))
    win = tk.Toplevel(root)
    win.title(title)
    input_box = scrolledtext.ScrolledText(win, width=60, height=10)
    input_box.pack(padx=10, pady=10)
    tk.Button(win, text="Save", bg="#800000", fg="white", font=("Arial", 10, "bold"),
              command=save_data).pack(pady=5)

def on_enter(e):
    e.widget['background'] = e.widget.hover_color

def on_leave(e):
    e.widget['background'] = e.widget.default_color

btn_style = {"font": ("Arial", 11, "bold"), "fg": "white", "height": 2, "width": 22, "relief": "flat", "cursor": "hand2"}

def make_button(parent, text, bg, hover_bg, cmd):
    btn = tk.Button(parent, text=text, bg=bg, **btn_style, command=cmd)
    btn.default_color = bg
    btn.hover_color = hover_bg
    btn.bind("<Enter>", on_enter)
    btn.bind("<Leave>", on_leave)
    btn.pack(pady=12, fill='x')
    return btn

btn_courses = make_button(button_frame, "📘 Add Courses", "#3498DB", "#2E86C1", lambda: show_input_window("Courses", courses))
btn_faculty = make_button(button_frame, "👨‍🏫 Add Faculty", "#27AE60", "#1E8449", lambda: show_input_window("Faculty", faculty))
btn_rooms = make_button(button_frame, "🏫 Add Rooms", "#F1C40F", "#D4AC0D", lambda: show_input_window("Rooms", rooms))
btn_slots = make_button(button_frame, "⏰ Add Slots", "#9B59B6", "#884EA0", lambda: show_input_window("Slots", slots))
btn_prefs = make_button(button_frame, "⭐ Preferred Slots", "#E67E22", "#CA6F1E", lambda: show_input_window("Preferred Slots", preferred_slots))
btn_load = make_button(button_frame, "📂 Load Inputs from File", "#2ECC71", "#28B463", load_inputs_from_file)

preview_frame = tk.Frame(main_frame, bg="#1C1C1C")
preview_frame.pack(side="right", padx=20, pady=10, fill="both", expand=True)

preview_box = scrolledtext.ScrolledText(preview_frame, width=85, height=25, font=("Consolas", 10))
preview_box.pack(fill="both", expand=True, padx=10, pady=10)

action_frame = tk.Frame(root, bg="#1C1C1C", height=80)
action_frame.pack(side="bottom", fill="x")
action_frame.pack_propagate(False)

btn_generate = tk.Button(action_frame, text="🚀 Generate Timetable & PDF", bg="#800000", fg="white",
                         font=("Arial", 12, "bold"), padx=20, pady=10, width=28, relief="flat",
                         command=generate_timetable)
btn_generate.pack(side="left", padx=80, pady=(5, 20))
btn_generate.default_color = "#800000"
btn_generate.hover_color = "#A93226"
btn_generate.bind("<Enter>", on_enter)
btn_generate.bind("<Leave>", on_leave)

btn_close = tk.Button(action_frame, text="❌ Close", bg="#E74C3C", fg="white",
                      font=("Arial", 12, "bold"), padx=20, pady=10, width=15, relief="flat",
                      command=root.destroy)
btn_close.pack(side="right", padx=80, pady=(5, 20))
btn_close.default_color = "#E74C3C"
btn_close.hover_color = "#C0392B"
btn_close.bind("<Enter>", on_enter)
btn_close.bind("<Leave>", on_leave)

root.mainloop()

import colorsys
import hashlib
import os
import sys
import warnings
import matplotlib.pyplot as plt
import requests
import fastf1
import fastf1.plotting

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)
fastf1.set_log_level("ERROR")  # it logs way too much otherwise

CACHE_DIR = "f1_cache"
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)
fastf1.Cache.enable_cache(CACHE_DIR)

fastf1.plotting.setup_mpl()

MIN_TELEMETRY_YEAR = 2018  # fastf1 has nothing real before this
MIN_LAP_DATA_YEAR = 1996
BASE_API_URL = "https://api.jolpi.ca/ergast/f1"

# not every team/era is covered here, older or obscure ones just fall
# back to a generated color instead of crashing
TEAM_COLORS = {
    "ferrari": "#E80020", "alfa": "#981E32", "maserati": "#1A3B68", "toyota": "#E4002B",
    "marussia": "#FF0000", "virgin": "#FF0000", "mercedes": "#00A19B", "bmw_sauber": "#0066B2",
    "haas": "#E60000", "honda": "#D1232A", "alphatauri": "#002F6C", "alpine": "#005EA6",
    "renault": "#FFF200", "red_bull": "#001A30", "williams": "#005A9C", "rb": "#0000FF",
    "sauber": "#00CC00", "prost": "#002FA7", "tyrrell": "#003366", "ligier": "#2B5491",
    "aston_martin": "#006F62", "jaguar": "#004225", "caterham": "#004F30", "lotus_racing": "#004F30",
    "vanwall": "#1B4D3E", "mclaren": "#FF8000", "jordan": "#E6D200", "benetton": "#009B77",
    "brawn": "#B8D400", "force_india": "#FFC0CB", "racing_point": "#FFC0CB",
}


def get_team_color(constructor_id):
    if constructor_id in TEAM_COLORS:
        return TEAM_COLORS[constructor_id]
    hash_val = int(hashlib.md5(constructor_id.encode()).hexdigest(), 16)
    hue = (hash_val % 360) / 360
    r, g, b = colorsys.hsv_to_rgb(hue, 0.65, 0.75)
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))


def find_historic_race(year, race_query):
    url = f"{BASE_API_URL}/{year}.json?limit=100"
    resp = requests.get(url)
    resp.raise_for_status()
    races = resp.json()["MRData"]["RaceTable"]["Races"]

    if not races:
        print(f"❌ No races found for {year}.")
        return None

    query = race_query.lower()
    matches = [
        r for r in races
        if query in r["raceName"].lower()
        or query in r["Circuit"]["circuitName"].lower()
        or query in r["Circuit"]["Location"]["country"].lower()
        or query in r["Circuit"]["Location"]["locality"].lower()
    ]

    if not matches:
        print(f"❌ No race matching '{race_query}' found in {year}.")
        return None

    return matches[0]


def get_historic_results(year, round_number):
    url = f"{BASE_API_URL}/{year}/{round_number}/results.json?limit=100"
    resp = requests.get(url)
    resp.raise_for_status()
    races = resp.json()["MRData"]["RaceTable"]["Races"]
    return races[0]["Results"] if races else None


def time_str_to_seconds(time_str):
    parts = time_str.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return float(parts[0])


def get_historic_laps(year, round_number):
    limit = 1000
    offset = 0
    driver_laps = {}

    while True:
        url = f"{BASE_API_URL}/{year}/{round_number}/laps.json?limit={limit}&offset={offset}"
        resp = requests.get(url)
        resp.raise_for_status()
        data = resp.json()["MRData"]
        total = int(data["total"])
        races = data["RaceTable"]["Races"]

        if not races:
            break

        for lap in races[0]["Laps"]:
            lap_number = int(lap["number"])
            for timing in lap["Timings"]:
                seconds = time_str_to_seconds(timing["time"])
                driver_laps.setdefault(timing["driverId"], []).append((lap_number, seconds))

        offset += limit
        if offset >= total:
            break

    for driver_id in driver_laps:
        driver_laps[driver_id].sort(key=lambda x: x[0])

    return driver_laps


def categorize_status(status):
    status_lower = status.lower()
    if status_lower == "finished" or status_lower.startswith("+"):
        return "Finished", "#1a7a1a"
    if status_lower == "disqualified":
        return "Disqualified", "#8B008B"
    if status_lower in ("accident", "collision", "collision damage", "spun off"):
        return "Accident / Crash", "#CC0000"
    if status_lower in ("did not qualify", "did not start", "did not prequalify", "withdrew"):
        return "Did Not Start/Qualify", "#888888"
    return "Retired (mechanical/other)", "#CC7722"


def select_historic_drivers(driver_laps, results):
    finish_order = [r["Driver"]["driverId"] for r in results]
    extra = [d for d in driver_laps if d not in finish_order]
    ranked_drivers = [d for d in finish_order if d in driver_laps] + extra
    total = len(ranked_drivers)

    print("\n--- DRIVER SELECTION ---")
    print(" 1. 🏎️  Plot ALL drivers (including DNFs/Retirements)")
    print(" 2. 🏆 Select top N finishers")
    choice = input("\nSelect option (1 or 2): ").strip()

    if choice == "2":
        while True:
            num_input = input(f"How many top drivers would you like to see? (1-{total}): ").strip()
            if num_input.isdigit() and 1 <= int(num_input) <= total:
                return ranked_drivers[:int(num_input)]
            print(f"Please enter a valid number between 1 and {total}.")

    return ranked_drivers


def plot_historic_lap_pace(driver_laps, results, race_name, year):
    selected_drivers = select_historic_drivers(driver_laps, results)
    plotted = {d: driver_laps[d] for d in selected_drivers if d in driver_laps}

    if not plotted:
        print("⚠️ Lap pace data is unavailable for this race.")
        return

    driver_team_map = {
        r["Driver"]["driverId"]: (r["Constructor"]["constructorId"], r["Constructor"]["name"])
        for r in results
    }
    finish_order = [r["Driver"]["driverId"] for r in results]

    team_to_drivers = {}
    for driver_id in plotted:
        constructor_id, constructor_name = driver_team_map.get(driver_id, ("unknown", "Unknown Team"))
        team_to_drivers.setdefault((constructor_id, constructor_name), []).append(driver_id)

    print(f"\nGenerating lap pace chart for: {', '.join(plotted.keys())}...")
    fig, ax = plt.subplots(figsize=(12, 6))
    line_styles = ["-", "--", ":"]

    for (constructor_id, _), drivers in team_to_drivers.items():
        color = get_team_color(constructor_id)
        drivers_sorted = sorted(
            drivers, key=lambda d: finish_order.index(d) if d in finish_order else 999
        )
        for style_index, driver_id in enumerate(drivers_sorted):
            laps = plotted[driver_id]
            lap_numbers = [lap for lap, _ in laps]
            lap_seconds = [seconds for _, seconds in laps]
            ax.plot(
                lap_numbers, lap_seconds,
                label=driver_id,
                color=color,
                linestyle=line_styles[style_index % len(line_styles)],
                linewidth=2,
            )

    ax.set_title(f"{year} {race_name} - Lap Pace Comparison", fontsize=14, fontweight="bold")
    ax.set_xlabel("Lap Number", fontsize=11)
    ax.set_ylabel("Lap Time (Seconds)", fontsize=11)
    ax.grid(True, linestyle=":", alpha=0.5)
    ax.legend(loc="upper right", fontsize=8)
    plt.tight_layout()
    plt.show()


def plot_historic_results(results, race_name, year):
    fig, ax = plt.subplots(figsize=(9, min(1.5 + 0.35 * len(results), 14)))
    ax.axis("off")
    ax.set_title(f"{year} {race_name} — Full Classification", fontsize=14, fontweight="bold", pad=20)

    medals = ["🥇", "🥈", "🥉"]
    podium_lines = []
    for i, r in enumerate(results[:3]):
        driver = f"{r['Driver']['givenName']} {r['Driver']['familyName']}"
        team = r["Constructor"]["name"]
        podium_lines.append(f"{medals[i]} {driver} ({team})")
    ax.text(0, 1.0, "Podium:\n" + "\n".join(podium_lines), fontsize=11,
            va="top", ha="left", transform=ax.transAxes, fontweight="bold")

    table_data = []
    cell_colors = []
    for r in results:
        position = r.get("positionText", "?")
        driver = f"{r['Driver']['givenName']} {r['Driver']['familyName']}"
        team = r["Constructor"]["name"]
        status = r.get("status", "Unknown")
        _, color = categorize_status(status)
        table_data.append([position, driver, team, status])
        cell_colors.append(["white", "white", "white", color])

    table = ax.table(
        cellText=table_data,
        colLabels=["Pos", "Driver", "Team", "Result"],
        loc="upper center",
        bbox=[0, -0.05, 1, 0.75 - 0.02 * len(podium_lines)],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(8)

    for row_index, row_colors in enumerate(cell_colors, start=1):
        for col_index, color in enumerate(row_colors):
            cell = table[row_index, col_index]
            if col_index == 3:
                cell.get_text().set_color(color)
                cell.get_text().set_fontweight("bold")

    plt.tight_layout()
    plt.show()


def run_historic_flow(year, race_query):
    # jolpica driverIds (e.g. "michael_schumacher") don't line up with
    # fastf1's 3-letter codes, so no "type a driver code" option here like
    # in the 2018+ menu below — just all/top-N for now
    race = find_historic_race(year, race_query)
    if not race:
        return

    print(f"🏁 Found: {race['raceName']} ({race['date']}) — fetching data...")
    results = get_historic_results(year, race["round"])
    if not results:
        print("⚠️ No race results available.")
        return

    has_lap_data = year >= MIN_LAP_DATA_YEAR
    driver_laps = get_historic_laps(year, race["round"]) if has_lap_data else {}
    if has_lap_data and not driver_laps:
        has_lap_data = False

    while True:
        print("\n" + "=" * 45)
        print(f"📊 ANALYSIS MENU: {year} {race['raceName']}")
        print("=" * 45)
        print(" 1. 📈 Lap Time Pace Progression Chart"
              + ("" if has_lap_data else "  (Unavailable for this year)"))
        print(" 2. 🏆 Full Classification (Podium, DNF, Crashes, DQs)")
        print(" 3. 🔄 Pick a different Grand Prix / Year")
        print(" 4. 🚪 Exit Program")

        choice = input("\nSelect an option (1-4): ").strip()

        if choice == "1":
            if not has_lap_data:
                print("⚠️ Lap pace and telemetry data are unavailable for this race year.")
            else:
                plot_historic_lap_pace(driver_laps, results, race["raceName"], year)
        elif choice == "2":
            plot_historic_results(results, race["raceName"], year)
        elif choice == "3":
            return
        elif choice == "4":
            print("\n👋 Thanks for using F1 Analytics! Goodbye.")
            sys.exit(0)
        else:
            print("Invalid selection. Please type 1, 2, 3, or 4.")


def get_driver_color_safe(driver, session):
    try:
        return fastf1.plotting.get_driver_color(driver, session=session)
    except Exception:
        return None


def load_session_fast(year, race_query, session_type="R"):
    # telemetry/weather/messages off here on purpose, we only need laps+results
    # and loading everything makes this take forever
    print(f"\n⚡ Fetching data for {year} '{race_query}'...", end=" ", flush=True)
    try:
        session = fastf1.get_session(year, race_query, session_type)
        session.load(telemetry=False, weather=False, messages=False)
        print("Done! ✅")
        return session
    except Exception as e:
        print(f"\n❌ Could not load session: {e}")
        return None


def get_driver_selection(session):
    all_drivers = list(session.results["Abbreviation"])
    total_drivers = len(all_drivers)

    print("\n--- DRIVER SELECTION ---")
    print(" 1. 🏎️  Plot ALL drivers (including retired/crashed drivers)")
    print(" 2. 🏆 Select top N finishers")
    
    choice = input("\nSelect option (1 or 2): ").strip()

    if choice == "1":
        return all_drivers

    elif choice == "2":
        while True:
            num_input = input(f"How many top drivers would you like to see? (1-{total_drivers}): ").strip()
            if num_input.isdigit() and 1 <= int(num_input) <= total_drivers:
                top_n = int(num_input)
                break
            print(f"Please enter a valid number between 1 and {total_drivers}.")

        selected_drivers = list(session.results.iloc[:top_n]["Abbreviation"])
        print(f"\nCurrently selected Top {top_n}: {', '.join(selected_drivers)}")

        add_more = input("\nAdd additional driver codes manually? (y/n): ").strip().lower()
        if add_more.startswith("y"):
            print(f"Available drivers: {', '.join(all_drivers)}")
            custom_input = input("Enter driver codes (e.g. RIC NOR ALO): ").strip().upper()
            
            for code in custom_input.split():
                if code in all_drivers and code not in selected_drivers:
                    selected_drivers.append(code)
                elif code not in all_drivers:
                    print(f"⚠️ Driver '{code}' was not found in this session.")

        return selected_drivers

    else:
        print("Invalid choice! Defaulting to Top 5 finishers.")
        return list(session.results.iloc[:5]["Abbreviation"])


def plot_lap_times(session):
    try:
        laps = session.laps
    except Exception:
        print("⚠️ Lap pace data is unavailable for this session.")
        return

    if laps.empty:
        print("⚠️ No lap data available.")
        return

    try:
        selected_drivers = get_driver_selection(session)
    except Exception as e:
        print(f"⚠️ Couldn't read driver results for this session ({e}).")
        return

    print(f"\nGenerating lap pace chart for: {', '.join(selected_drivers)}...")
    fig, ax = plt.subplots(figsize=(12, 6))

    for drv in selected_drivers:
        # not using pick_quicklaps() here on purpose — that drops slow
        # laps too, which hides DNF drivers almost entirely. tradeoff is a
        # safety car lap can spike the y-axis, live with it for now
        drv_laps = laps.pick_drivers(drv)
        drv_laps = drv_laps.dropna(subset=["LapTime"])

        if drv_laps.empty:
            continue

        lap_seconds = drv_laps["LapTime"].dt.total_seconds()
        color = get_driver_color_safe(drv, session)

        ax.plot(
            drv_laps["LapNumber"],
            lap_seconds,
            label=drv,
            color=color,
            linewidth=2
        )

    ax.set_title(f"{session.event.year} {session.event['EventName']} - Lap Pace Comparison", fontsize=14, fontweight="bold")
    ax.set_xlabel("Lap Number", fontsize=11)
    ax.set_ylabel("Lap Time (Seconds)", fontsize=11)
    ax.grid(True, linestyle=":", alpha=0.5)
    ax.legend(loc="upper right")
    plt.tight_layout()
    plt.show()


def plot_telemetry(session):
    print("\n⚡ Fetching detailed telemetry stream...", end=" ", flush=True)
    try:
        session.load(telemetry=True)
        print("Done! ✅")
    except Exception:
        print("\n⚠️ Detailed telemetry trace is unavailable for this session.")
        return

    d1 = input("Enter 1st Driver Code (e.g. VER): ").strip().upper()
    d2 = input("Enter 2nd Driver Code to compare (or press Enter for solo): ").strip().upper()

    try:
        d1_laps = session.laps.pick_drivers(d1)
    except Exception as e:
        print(f"⚠️ Couldn't read lap data for this session ({e}).")
        return

    if d1_laps.empty:
        print(f"❌ No recorded laps found for {d1}.")
        return

    try:
        d1_fastest = d1_laps.pick_fastest()
        d1_tel = d1_fastest.get_telemetry()
    except Exception as e:
        print(f"⚠️ Couldn't load telemetry for {d1} ({e}).")
        return

    d1_color = get_driver_color_safe(d1, session) or "cyan"

    fig, axs = plt.subplots(5, 1, figsize=(14, 10), sharex=True)
    d1_time = d1_fastest["LapTime"].total_seconds()
    
    fig.suptitle(
        f"{session.event.year} {session.event['EventName']} — Telemetry Trace\n"
        f"{d1} Fastest Lap ({d1_time:.3f}s)",
        fontsize=14, fontweight="bold"
    )

    axs[0].plot(d1_tel["Distance"], d1_tel["Speed"], color=d1_color, label=f"{d1}")
    axs[1].plot(d1_tel["Distance"], d1_tel["RPM"], color=d1_color)
    axs[2].plot(d1_tel["Distance"], d1_tel["Throttle"], color=d1_color)
    axs[3].plot(d1_tel["Distance"], d1_tel["DRS"], color=d1_color)
    axs[4].plot(d1_tel["Distance"], d1_tel["nGear"], color=d1_color)

    if d2:
        try:
            d2_laps = session.laps.pick_drivers(d2)
            if not d2_laps.empty:
                d2_fastest = d2_laps.pick_fastest()
                d2_tel = d2_fastest.get_telemetry()
                d2_color = get_driver_color_safe(d2, session) or "magenta"
                d2_time = d2_fastest["LapTime"].total_seconds()

                axs[0].plot(d2_tel["Distance"], d2_tel["Speed"], color=d2_color, label=f"{d2} ({d2_time:.3f}s)")
                axs[1].plot(d2_tel["Distance"], d2_tel["RPM"], color=d2_color)
                axs[2].plot(d2_tel["Distance"], d2_tel["Throttle"], color=d2_color)
                axs[3].plot(d2_tel["Distance"], d2_tel["DRS"], color=d2_color)
                axs[4].plot(d2_tel["Distance"], d2_tel["nGear"], color=d2_color)
        except Exception:
            print(f"⚠️ Telemetry for {d2} could not be loaded — rendering {d1} only.")

    y_labels = ["Speed (km/h)", "RPM", "Throttle %", "DRS", "Gear"]
    for i, label in enumerate(y_labels):
        axs[i].set_ylabel(label)
        axs[i].grid(True, linestyle=":", alpha=0.4)

    axs[4].set_xlabel("Track Distance (Meters)")
    axs[0].legend(loc="upper right")
    plt.tight_layout()
    plt.show()


def session_analysis_menu(session):
    try:
        event_label = f"{session.event.year} {session.event['EventName']}"
    except Exception:
        event_label = "this session"

    while True:
        print("\n" + "=" * 45)
        print(f"📊 ANALYSIS MENU: {event_label}")
        print("=" * 45)
        print(" 1. 📈 Lap Time Pace Progression Chart")
        print(" 2. ⚡ Full 5-Panel Telemetry Trace")
        print(" 3. 🔄 Pick a different Grand Prix / Year")
        print(" 4. 🚪 Exit Program")

        choice = input("\nSelect an option (1-4): ").strip()

        if choice == "1":
            plot_lap_times(session)
        elif choice == "2":
            plot_telemetry(session)
        elif choice == "3":
            return True
        elif choice == "4":
            print("\n👋 Thanks for using F1 Analytics! Goodbye.")
            sys.exit(0)
        else:
            print("Invalid selection. Please type 1, 2, 3, or 4.")


def main():
    print("==================================================")
    print(" 🏎️  F1 PACING & TELEMETRY Analysis")
    print("==================================================")

    while True:
        year_in = input("\nEnter Year (e.g. 2021, 2024) [or 'q' to exit]: ").strip().lower()
        if year_in == 'q':
            print("\n👋 Goodbye!")
            break

        if not year_in.isdigit():
            print("Please enter a valid numeric year.")
            continue

        race_query = input("Enter Race Name or Country (e.g. Monza, Monaco, Silverstone): ").strip()
        if not race_query:
            print("Race name cannot be empty.")
            continue

        year = int(year_in)

        if year >= MIN_TELEMETRY_YEAR:
            session = load_session_fast(year, race_query, "R")
            if not session:
                continue
            session_analysis_menu(session)
        else:
            run_historic_flow(year, race_query)


if __name__ == "__main__":
    main()

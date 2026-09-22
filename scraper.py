import json
import re
import time
from urllib import request
from playwright.sync_api import sync_playwright


# ============================================================
# KONFIGURASI
# ============================================================

API_URL = "https://telemetri-bbws-pomjen.com/KA/api_sensor_xylem.php"

EAGLE_URL = "https://public.eagle.io/public/dash/etpvkt0ofbbt6mt"

# Waktu tunggu setelah halaman dibuka agar WebSocket/data Eagle.io
# mempunyai waktu untuk memuat data.
WAIT_AFTER_LOAD = 15


# ============================================================
# FUNGSI EKSTRAK ANGKA
# ============================================================

def extract_number(text):
    """
    Mengambil angka pertama dari sebuah teks.

    Contoh:
        '27.146 DegreesC' -> 27.146
        '390.075 FNU'     -> 390.075
        '55 mA'           -> 55.0
    """

    if not text:
        return 0.0

    match = re.search(r"-?\d+\.\d+|-?\d+", text)

    return float(match.group()) if match else 0.0


def extract_device_val(line_text, keyword):
    """
    Mengambil angka setelah keyword perangkat.

    Contoh:
        'BatteryVoltage 13.39 Volts'
        -> 13.39

        'CurrentMaximum 55 mA'
        -> 55.0
    """

    try:
        parts = line_text.split(keyword)

        if len(parts) > 1:
            match = re.search(
                r"-?\d+\.\d+|-?\d+",
                parts[1]
            )

            if match:
                return float(match.group())

    except Exception:
        pass

    return 0.0


# ============================================================
# FUNGSI EKSTRAK TIMESTAMP
# ============================================================

def extract_timestamp(text):
    """
    Mengambil TIMESTAMP LENGKAP dari halaman Eagle.io.

    Contoh:
        'SondeValues - ODO Sat 69.133 % NORMAL 2026-09-22 08:00:00'

    menghasilkan:

        '2026-09-22 08:00:00'

    PENTING:
    - Tidak menggunakan datetime.now()
    - Tidak melakukan pengurangan/penambahan jam
    - Tidak melakukan konversi timezone
    - Menggunakan timestamp persis seperti yang ditampilkan
      oleh website sumber.
    """

    if not text:
        return None

    match = re.search(
        r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}",
        text
    )

    if match:
        return match.group(0)

    return None


# ============================================================
# FUNGSI KIRIM DATA KE PHP API
# ============================================================

def send_to_php_api(device_data, wq_data):

    payload = {}

    if device_data:
        payload["device_health"] = device_data

    if wq_data:
        payload["wq_ms"] = wq_data

    if not payload:
        print("[WARNING] Tidak ada data yang akan dikirim.")
        return

    print()
    print("=" * 70)
    print("[INFO] DATA YANG AKAN DIKIRIM KE PHP API")
    print("=" * 70)

    print(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False
        )
    )

    print("=" * 70)

    try:

        data_json = json.dumps(
            payload,
            ensure_ascii=False
        ).encode("utf-8")

        req = request.Request(
            API_URL,
            data=data_json,
            headers={
                "Content-Type": "application/json"
            },
            method="POST",
        )

        with request.urlopen(req, timeout=60) as response:

            result = response.read().decode("utf-8")

            print()
            print("[INFO] Respon server PHP:")
            print(result)

    except Exception as e:

        print()
        print(
            f"[ERROR] Gagal mengirim data ke API PHP: {e}"
        )


# ============================================================
# SCRAPER UTAMA
# ============================================================

def scrape_and_sync():

    print()
    print("=" * 70)
    print("MEMULAI SCRAPER XYLEM / EAGLE.IO")
    print("=" * 70)

    device_data = {}
    wq_data = {}

    txt_report_lines = []

    with sync_playwright() as p:

        browser = None

        try:

            # ------------------------------------------------
            # BUKA BROWSER
            # ------------------------------------------------

            browser = p.chromium.launch(
                headless=True
            )

            page = browser.new_page()

            print()
            print("[INFO] Membuka halaman Eagle.io:")
            print(EAGLE_URL)

            page.goto(
                EAGLE_URL,
                wait_until="domcontentloaded",
                timeout=60000
            )

            print(
                "[INFO] Halaman berhasil dibuka."
            )

            print(
                "[INFO] Menunggu halaman memuat data WebSocket..."
            )

            time.sleep(WAIT_AFTER_LOAD)

            # ------------------------------------------------
            # AMBIL TEXT DARI HALAMAN
            # ------------------------------------------------

           page_text = page.inner_text("body")

            print("\n" + "=" * 80)
            print("RAW BARIS WQMS YANG DIBACA PLAYWRIGHT")
            print("=" * 80)
            
            for line in page_text.split("\n"):
                line = line.strip()
            
                if any(keyword.lower() in line.lower() for keyword in [
                    "ODO Sat",
                    "External Temp",
                    "Turbidity",
                    "Salinity",
                    "Chlorophyll",
                    "BGA PC",
                    "fDOM"
                ]):
                    print(repr(line))
            
            print("=" * 80)
            
            print("\n" + "=" * 80)
            print("SEMUA TIMESTAMP YANG DITEMUKAN PLAYWRIGHT")
            print("=" * 80)
            
            timestamps = re.findall(
                r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}",
                page_text
            )
            
            for ts in timestamps:
                print(ts)
            
            print("=" * 80 + "\n")

            lines = [
                line.strip()
                for line in page_text.split("\n")
                if line.strip()
            ]

            print()
            print(
                f"[INFO] Jumlah baris yang dibaca: {len(lines)}"
            )

            # ------------------------------------------------
            # PROSES SETIAP BARIS
            # ------------------------------------------------

            for i, line in enumerate(lines):

                # ------------------------------------------------
                # Abaikan thermistor
                # ------------------------------------------------

                if "thermistor" in line.lower():
                    continue

                sensor_info = line

                # ------------------------------------------------
                # Beberapa struktur halaman dapat memisahkan
                # nama sensor dan nilainya ke baris berbeda.
                #
                # Pertahankan mekanisme dari scraper lama.
                # ------------------------------------------------

                if not any(
                    u in line
                    for u in [
                        "Volts",
                        "mA",
                        "°C",
                        "%",
                        "FNU",
                        "ppm",
                        "ug/L",
                        "RFU",
                        "Deg C",
                        "DegreesC",
                    ]
                ):

                    if i + 1 < len(lines):

                        sensor_info = (
                            f"{line} : {lines[i + 1]}"
                        )

                # ------------------------------------------------
                # CARI TIMESTAMP LENGKAP
                #
                # Contoh:
                # 2026-09-22 08:00:00
                # ------------------------------------------------

                full_timestamp = extract_timestamp(
                    sensor_info
                )

                # ------------------------------------------------
                # Jika timestamp tidak ada pada sensor_info,
                # coba cari timestamp pada beberapa baris
                # di sekitar baris tersebut.
                #
                # Ini untuk mengantisipasi struktur DOM Eagle.io
                # yang memisahkan data menjadi beberapa baris.
                # ------------------------------------------------

                if not full_timestamp:

                    context_lines = [
                        line
                    ]

                    if i > 0:
                        context_lines.append(
                            lines[i - 1]
                        )

                    if i + 1 < len(lines):
                        context_lines.append(
                            lines[i + 1]
                        )

                    if i + 2 < len(lines):
                        context_lines.append(
                            lines[i + 2]
                        )

                    context_text = " ".join(
                        context_lines
                    )

                    full_timestamp = extract_timestamp(
                        context_text
                    )

                # ------------------------------------------------
                # Jika timestamp benar-benar tidak ditemukan,
                # jangan membuat timestamp sendiri.
                #
                # Data tidak diberi timestamp palsu.
                # ------------------------------------------------

                if not full_timestamp:
                    continue

                # =================================================
                # DEVICE HEALTH
                # =================================================

                if "BatteryVoltage" in sensor_info:

                    val = extract_device_val(
                        sensor_info,
                        "BatteryVoltage"
                    )

                    device_data[
                        "battery_voltage"
                    ] = val

                    device_data[
                        "timestamp"
                    ] = full_timestamp

                    device_data[
                        "status"
                    ] = "NORMAL"

                    txt_report_lines.append(
                        f"Ai1 - BatteryVoltage\t"
                        f"{val} Volts\t"
                        f"NORMAL\t"
                        f"{full_timestamp}"
                    )

                    print(
                        f"[TIMESTAMP][DEVICE] "
                        f"BatteryVoltage -> "
                        f"{full_timestamp}"
                    )

                elif "CurrentMaximum" in sensor_info:

                    val = extract_device_val(
                        sensor_info,
                        "CurrentMaximum"
                    )

                    device_data[
                        "current_maximum"
                    ] = val

                    txt_report_lines.append(
                        f"Ai1 - CurrentMaximum\t"
                        f"{val} mA\t"
                        f"NORMAL\t"
                        f"{full_timestamp}"
                    )

                    print(
                        f"[TIMESTAMP][DEVICE] "
                        f"CurrentMaximum -> "
                        f"{full_timestamp}"
                    )

                elif "InternalHumidity" in sensor_info:

                    val = extract_device_val(
                        sensor_info,
                        "InternalHumidity"
                    )

                    device_data[
                        "internal_humidity"
                    ] = val

                    txt_report_lines.append(
                        f"Ai1 - InternalHumidity\t"
                        f"{val} %\t"
                        f"NORMAL\t"
                        f"{full_timestamp}"
                    )

                    print(
                        f"[TIMESTAMP][DEVICE] "
                        f"InternalHumidity -> "
                        f"{full_timestamp}"
                    )

                elif "InternalTemperature" in sensor_info:

                    val = extract_device_val(
                        sensor_info,
                        "InternalTemperature"
                    )

                    device_data[
                        "internal_temperature"
                    ] = val

                    txt_report_lines.append(
                        f"Ai1 - InternalTemperature\t"
                        f"{val} °C\t"
                        f"NORMAL\t"
                        f"{full_timestamp}"
                    )

                    print(
                        f"[TIMESTAMP][DEVICE] "
                        f"InternalTemperature -> "
                        f"{full_timestamp}"
                    )

                # =================================================
                # WATER QUALITY - WQMS
                # =================================================

                elif "BGA PC" in sensor_info:

                    val = extract_number(
                        sensor_info
                    )

                    wq_data[
                        "bga_pc"
                    ] = val

                    wq_data[
                        "timestamp"
                    ] = full_timestamp

                    wq_data[
                        "status"
                    ] = "NORMAL"

                    txt_report_lines.append(
                        f"SondeValues - BGA PC ugL\t"
                        f"{val} ug/L\t"
                        f"NORMAL\t"
                        f"{full_timestamp}"
                    )

                    print(
                        f"[TIMESTAMP][WQMS] "
                        f"BGA PC -> "
                        f"{full_timestamp}"
                    )

                elif "Chlorophyll" in sensor_info:

                    val = extract_number(
                        sensor_info
                    )

                    wq_data[
                        "chlorophyll"
                    ] = val

                    txt_report_lines.append(
                        f"SondeValues - Chlorophyll ugL\t"
                        f"{val} ug/L\t"
                        f"NORMAL\t"
                        f"{full_timestamp}"
                    )

                    print(
                        f"[TIMESTAMP][WQMS] "
                        f"Chlorophyll -> "
                        f"{full_timestamp}"
                    )

                elif "External Temp" in sensor_info:

                    val = extract_number(
                        sensor_info
                    )

                    wq_data[
                        "external_temp"
                    ] = val

                    txt_report_lines.append(
                        f"SondeValues - External Temp\t"
                        f"{val} DegreesC\t"
                        f"NORMAL\t"
                        f"{full_timestamp}"
                    )

                    print(
                        f"[TIMESTAMP][WQMS] "
                        f"External Temp -> "
                        f"{full_timestamp}"
                    )

                elif "ODO Sat" in sensor_info:

                    val = extract_number(
                        sensor_info
                    )

                    wq_data[
                        "odo_sat"
                    ] = val

                    txt_report_lines.append(
                        f"SondeValues - ODO Sat\t"
                        f"{val} %\t"
                        f"NORMAL\t"
                        f"{full_timestamp}"
                    )

                    print(
                        f"[TIMESTAMP][WQMS] "
                        f"ODO Sat -> "
                        f"{full_timestamp}"
                    )

                elif "Salinity" in sensor_info:

                    val = extract_number(
                        sensor_info
                    )

                    wq_data[
                        "salinity"
                    ] = val

                    txt_report_lines.append(
                        f"SondeValues - Salinity\t"
                        f"{val} ppm\t"
                        f"NORMAL\t"
                        f"{full_timestamp}"
                    )

                    print(
                        f"[TIMESTAMP][WQMS] "
                        f"Salinity -> "
                        f"{full_timestamp}"
                    )

                elif "Turbidity" in sensor_info:

                    val = extract_number(
                        sensor_info
                    )

                    # Mencegah pembacaan angka yang salah
                    # akibat baris duplikat / timestamp.
                    if "2026." not in str(val):

                        wq_data[
                            "turbidity"
                        ] = val

                        txt_report_lines.append(
                            f"SondeValues - Turbidity\t"
                            f"{val} FNU\t"
                            f"NORMAL\t"
                            f"{full_timestamp}"
                        )

                        print(
                            f"[TIMESTAMP][WQMS] "
                            f"Turbidity -> "
                            f"{full_timestamp}"
                        )

                elif "fDOM" in sensor_info:

                    val = extract_number(
                        sensor_info
                    )

                    wq_data[
                        "fdom"
                    ] = val

                    txt_report_lines.append(
                        f"SondeValues - fDOM RFU\t"
                        f"{val} RFU\t"
                        f"NORMAL\t"
                        f"{full_timestamp}"
                    )

                    print(
                        f"[TIMESTAMP][WQMS] "
                        f"fDOM -> "
                        f"{full_timestamp}"
                    )

            # =====================================================
            # SIMPAN LAPORAN TXT
            # =====================================================

            with open(
                "nilaisensor.txt",
                "w",
                encoding="utf-8"
            ) as f:

                f.write(
                    "LAPORAN NILAI SENSOR\n"
                )

                f.write(
                    "=" * 70 + "\n"
                )

                f.write(
                    "Timestamp pada laporan adalah "
                    "timestamp asli dari Eagle.io.\n"
                )

                f.write(
                    "=" * 70 + "\n\n"
                )

                for item in sorted(
                    set(txt_report_lines)
                ):

                    f.write(
                        item + "\n"
                    )

            # =====================================================
            # TUTUP BROWSER
            # =====================================================

        except Exception as e:

            print()
            print(
                f"[ERROR] Terjadi kesalahan scraper: {e}"
            )

        finally:

            if browser:

                browser.close()

                print()
                print(
                    "[INFO] Browser ditutup."
                )

    # ============================================================
    # TAMPILKAN DATA HASIL SCRAPING
    # ============================================================

    print()
    print("=" * 70)
    print("HASIL SCRAPING")
    print("=" * 70)

    print()
    print("[DEBUG] Data Device Health:")

    print(
        json.dumps(
            device_data,
            indent=2,
            ensure_ascii=False
        )
    )

    print()
    print("[DEBUG] Data WQMS:")

    print(
        json.dumps(
            wq_data,
            indent=2,
            ensure_ascii=False
        )
    )

    print("=" * 70)

    # ============================================================
    # VALIDASI TIMESTAMP
    # ============================================================

    if device_data.get("timestamp"):

        print(
            "[CHECK] Timestamp Device Health : "
            f"{device_data['timestamp']}"
        )

    else:

        print(
            "[WARNING] Timestamp Device Health "
            "tidak ditemukan."
        )

    if wq_data.get("timestamp"):

        print(
            "[CHECK] Timestamp WQMS          : "
            f"{wq_data['timestamp']}"
        )

    else:

        print(
            "[WARNING] Timestamp WQMS "
            "tidak ditemukan."
        )

    # ============================================================
    # KIRIM DATA
    # ============================================================

    if device_data or wq_data:

        send_to_php_api(
            device_data,
            wq_data
        )

    else:

        print(
            "[WARNING] Tidak ada data sensor "
            "yang valid untuk dikirim."
        )


# ================================================================
# MAIN
# ================================================================

if __name__ == "__main__":

    scrape_and_sync()

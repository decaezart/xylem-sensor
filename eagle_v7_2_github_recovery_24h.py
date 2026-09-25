# -*- coding: utf-8 -*-
"""
==============================================================================
EAGLE V7.4 API TEST - WQMS + DEVICE HEALTH + WEATHER + PRESSURE + DEPTH
GITHUB ACTIONS / OPERATIONAL MODE
==============================================================================

BASELINE:
    V4.5.5 = metode ekstraksi RAW yang sudah terbukti menghasilkan data
    historical reference = validasi dataset yang sudah PASS

TUJUAN:
    Mengambil langsung 7 parameter WQMS + 4 parameter device health + 2 parameter weather dari
    Eagle.io melalui WebSocket native Playwright, menggunakan metode RAW
    yang sudah terbukti.

MODE:
    TEST_MODE       = True
    API_POST        = True
    DATABASE_INSERT = False

    RANGE           = rolling 24 jam otomatis (Asia/Makassar)

TIDAK DILAKUKAN:
    - interpolasi
    - smoothing
    - fill gap
    - pembulatan timestamp
    - perubahan nilai sensor

RAW EAGLE:
    aggregate = NONE
    interval = tidak dikirim
    intervalInclude = tidak dikirim
    baseTime = tidak dikirim

TIMESTAMP:
    ts.$millis -> UTC
    timestamp_millis dipertahankan

OUTPUT:
    GitHub Actions log only; no persistent output files.

Install:
    pip install playwright pandas numpy
    playwright install chromium
"""

import asyncio
import base64
import gzip
import json
import math
import traceback
import uuid
from collections import Counter
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import numpy as np
import pandas as pd
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


# ==============================================================================
# 1. KONFIGURASI
# ==============================================================================

TEST_MODE = True
API_POST = True
DATABASE_INSERT = False

EAGLE_URL = (
    "https://public.eagle.io/public/dash/"
    "etpvkt0ofbbt6mt?timezone=Asia/Makassar"
)

TIMEZONE_ID = "Asia/Makassar"
PUBLIC_ID = "etpvkt0ofbbt6mt"
NODE_ID = "69ca1895c3d4343ae57b8ae1"

# ==========================================================================
# RENTANG DATA OPERASIONAL GITHUB - RECOVERY WINDOW 24 JAM
# ==========================================================================
# Setiap run mengambil 24 jam terakhir sampai waktu run.
# Contoh 24 Sep 10:17 WITA: START 23 Sep 10:17, END 24 Sep 10:17.
# END bersifat exclusive. Overlap ini berfungsi sebagai recovery window.
RANGE_TIMEZONE = "Asia/Makassar"
LOCAL_TZ = ZoneInfo(RANGE_TIMEZONE)

# ==========================================================================
# RANGE OPERASIONAL OTOMATIS - ROLLING 24 JAM
#
# Setiap eksekusi mengambil 24 jam terakhir berdasarkan waktu WITA.
# Contoh jika job berjalan 25-09-2026 10:17 WITA:
#   START = 24-09-2026 10:17 WITA
#   END   = 25-09-2026 10:17 WITA
#
# END bersifat EXCLUSIVE.
# Overlap 24 jam ini berfungsi sebagai recovery window: record yang sudah
# pernah dikirim akan di-UPSERT berdasarkan timestamp, sedangkan record
# yang terlambat masuk ke Eagle akan tertangkap pada run berikutnya.
# ==========================================================================
RANGE_END_EXCLUSIVE_LOCAL_DT = datetime.now(LOCAL_TZ)
RANGE_START_LOCAL_DT = RANGE_END_EXCLUSIVE_LOCAL_DT - timedelta(hours=24)

if RANGE_END_EXCLUSIVE_LOCAL_DT <= RANGE_START_LOCAL_DT:
    raise RuntimeError(
        "RANGE_END_EXCLUSIVE_LOCAL_DT harus lebih besar dari RANGE_START_LOCAL_DT."
    )

RANGE_START_LOCAL = RANGE_START_LOCAL_DT.strftime("%Y-%m-%d %H:%M:%S")
RANGE_END_EXCLUSIVE_LOCAL = RANGE_END_EXCLUSIVE_LOCAL_DT.strftime("%Y-%m-%d %H:%M:%S")
RANGE_START_UTC = RANGE_START_LOCAL_DT.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
RANGE_END_EXCLUSIVE_UTC = RANGE_END_EXCLUSIVE_LOCAL_DT.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
RUN_DATE_TAG = (
    f"{RANGE_START_LOCAL_DT.strftime('%Y%m%d_%H%M%S')}"
    f"_{RANGE_END_EXCLUSIVE_LOCAL_DT.strftime('%Y%m%d_%H%M%S')}"
)


HEADLESS = True

PAGE_TIMEOUT_MS = 120_000
NATIVE_WAIT_MS = 45_000
CUSTOM_WAIT_MS = 60_000

# Tunggu sebentar setelah halaman mulai aktif.
DASHBOARD_WAIT_MS = 10_000

# Jeda antar request parameter.
REQUEST_DELAY_MS = 500

# Retry hanya jika extraction reply tidak ditemukan.
MAX_RETRIES = 2

CUSTOM_ID_START = 18501

# GitHub Actions: hasil hanya digunakan selama job berjalan; tidak disimpan ke repository.
OUTPUT_DIR = Path("/tmp/eagle_v74")
OUTPUT_CSV = OUTPUT_DIR / f"eagle_v7_4_wqms_{RUN_DATE_TAG}.csv"
OUTPUT_REPORT = OUTPUT_DIR / f"eagle_v7_4_report_{RUN_DATE_TAG}.txt"
OUTPUT_RAW = OUTPUT_DIR / f"eagle_v7_4_raw_{RUN_DATE_TAG}.txt"
OUTPUT_CRASH = OUTPUT_DIR / f"eagle_v7_4_crash_{RUN_DATE_TAG}.log"

API_URL = "https://telemetri-bbws-pomjen.com/KA/api_sensor_xylem.php"
API_SEND_ALL = True
API_TEST_LIMIT = None
API_TIMEOUT_SECONDS = 30
OUTPUT_API_RESULT = OUTPUT_DIR / f"eagle_v7_4_api_result_{RUN_DATE_TAG}.txt"


# ==============================================================================
# 2. POINT WQMS + DEVICE HEALTH
# ==============================================================================

TARGET_POINTS = {
    "external_temp": "69d9c2afde6c9145418ca776",
    "odo_sat": "69d9c2afde6c9145418ca77e",
    "salinity": "69d9c2afde6c9145418ca77a",
    "turbidity": "69d9c2afde6c9145418ca784",
    "chlorophyll": "69d9c2afde6c9145418ca78e",
    "bga_pc": "69d9c2afde6c9145418ca797",
    "fdom": "69d9c2afde6c9145418ca799",
}

DEVICE_HEALTH_POINTS = {
    "battery_voltage": "68d0a84a3b27a910af7989b3",
    "internal_temperature": "68d0a84a3b27a910af7989b5",
    "internal_humidity": "68d0a84a3b27a910af7989b7",
    "current_maximum": "68d0a84a3b27a910af7989bb",
}

WEATHER_POINTS = {
    "temp_ambient": "69d9c2afde6c9145418ca7af",
    "humidity_ambient": "69d9c2afde6c9145418ca7b0",
}

PRESSURE_POINTS = {
    "barometric_pressure": "6a878f41138fdb53b30e39ec",
}

DEPTH_POINTS = {
    "depth": "6a8708b4138fdb53b3fc365e",
}

WEATHER_PARAMETERS = [
    "temp_ambient",
    "humidity_ambient",
]

WEATHER_POINT_TO_PARAMETER = {
    point_id: parameter
    for parameter, point_id in WEATHER_POINTS.items()
}

PRESSURE_POINT_TO_PARAMETER = {
    point_id: parameter
    for parameter, point_id in PRESSURE_POINTS.items()
}

DEPTH_POINT_TO_PARAMETER = {
    point_id: parameter
    for parameter, point_id in DEPTH_POINTS.items()
}

AUX_PARAMETERS = [
    "barometric_pressure",
    "depth",
]
AUX_POINT_TO_PARAMETER = {
    **PRESSURE_POINT_TO_PARAMETER,
    **DEPTH_POINT_TO_PARAMETER,
}

PARAMETERS = [
    "external_temp",
    "odo_sat",
    "salinity",
    "turbidity",
    "chlorophyll",
    "bga_pc",
    "fdom",
]

POINT_TO_PARAMETER = {
    point_id: parameter
    for parameter, point_id in TARGET_POINTS.items()
}

DEVICE_POINT_TO_PARAMETER = {
    point_id: parameter
    for parameter, point_id in DEVICE_HEALTH_POINTS.items()
}

DEVICE_HEALTH_PARAMETERS = [
    "battery_voltage",
    "internal_temperature",
    "internal_humidity",
    "current_maximum",
]

EXPECTED_COLUMNS = [
    "timestamp_utc",
    "timestamp_millis",
    "external_temp",
    "odo_sat",
    "salinity",
    "turbidity",
    "chlorophyll",
    "bga_pc",
    "fdom",
]


# ==============================================================================
# 3. STATE
# ==============================================================================

LOG_LINES = []

NATIVE_REQUEST = None
NATIVE_REQUEST_CAPTURED = False

CUSTOM_RESULTS = {}
DEVICE_HEALTH_RESULTS = {}
WEATHER_RESULTS = {}
AUX_RESULTS = {}

ALL_SENT_FRAMES = []
ALL_RECEIVED_FRAMES = []

CURRENT_PAGE = None


# ==============================================================================
# 4. LOG
# ==============================================================================

def log(*args):
    text = " ".join(str(x) for x in args)
    line = (
        f"[{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S')}] "
        f"{text}"
    )
    print(line, flush=True)
    LOG_LINES.append(line)


def section(title):
    print("\n" + "=" * 78, flush=True)
    print(title, flush=True)
    print("=" * 78, flush=True)


def save_text(path, text):
    # GitHub Actions: intentionally no persistent file output.
    return None


# ==============================================================================
# 5. SOCKET.IO FRAME
# ==============================================================================

def parse_frame(frame):
    if not isinstance(frame, str):
        return None

    frame = frame.strip()

    if not frame:
        return None

    start = frame.find("{")

    if start < 0:
        return None

    for end in range(len(frame), start, -1):

        if not frame[end - 1:end] == "}":
            continue

        candidate = frame[start:end]

        try:
            obj = json.loads(candidate)

            return {
                "raw": frame,
                "prefix": frame[:start],
                "obj": obj,
            }

        except Exception:
            continue

    return None


# ==============================================================================
# 6. DECODE RESPONSE
# ==============================================================================

def decode_gzip_base64(value):
    raw = base64.b64decode(value)
    data = gzip.decompress(raw)
    return data.decode("utf-8")


def decode_extraction_frame(frame):
    parsed = parse_frame(frame)

    if not parsed:
        return None, None, "frame bukan JSON"

    obj = parsed["obj"]
    payload = obj.get("p")

    if not isinstance(payload, list) or not payload:
        return None, None, "payload kosong"

    compressed = payload[0]

    if not isinstance(compressed, str):
        return None, None, "compressed payload bukan string"

    try:
        wrapper_text = decode_gzip_base64(
            compressed
        )

        wrapper = json.loads(
            wrapper_text
        )

    except Exception as exc:
        return None, None, (
            f"wrapper decode gagal: {exc}"
        )

    data = wrapper.get("data")

    if isinstance(data, str):

        try:
            jts = json.loads(data)

        except Exception as exc:
            return (
                wrapper,
                None,
                f"JTS JSON gagal: {exc}",
            )

    elif isinstance(data, dict):

        jts = data

    else:

        return (
            wrapper,
            None,
            "wrapper.data tidak ada",
        )

    return wrapper, jts, None


# ==============================================================================
# 7. JTS
# ==============================================================================

def millis_to_utc(millis):
    return datetime.fromtimestamp(
        int(millis) / 1000.0,
        tz=timezone.utc,
    )


def parse_jts(jts, parameter):
    if not isinstance(jts, dict):
        raise ValueError("JTS bukan object.")

    if jts.get("docType") != "jts":
        raise ValueError(
            f"docType={jts.get('docType')}"
        )

    if jts.get("subType") != "TIMESERIES":
        raise ValueError(
            f"subType={jts.get('subType')}"
        )

    header = jts.get("header")

    if not isinstance(header, dict):
        raise ValueError("JTS header tidak ada.")

    columns = header.get("columns")

    if not isinstance(columns, dict) or not columns:
        raise ValueError("JTS columns tidak ada.")

    column_key = next(
        iter(columns.keys())
    )

    column = columns[column_key]

    if not isinstance(column, dict):
        raise ValueError("JTS column invalid.")

    records = jts.get("data")

    if not isinstance(records, list):
        raise ValueError("JTS data bukan list.")

    output = []

    for record in records:

        if not isinstance(record, dict):
            continue

        ts = record.get("ts", {})

        if not isinstance(ts, dict):
            continue

        millis = ts.get("$millis")

        if millis is None:
            continue

        try:
            millis = int(millis)

        except Exception:
            continue

        fields = record.get("f", {})

        if not isinstance(fields, dict):
            fields = {}

        field = fields.get(
            str(column_key)
        )

        if not isinstance(field, dict):
            value = None
            quality = None

        else:
            value = field.get("v")
            quality = field.get("q")

        output.append(
            {
                "timestamp_millis": millis,
                "timestamp_utc": millis_to_utc(
                    millis
                ),
                "value": value,
                "quality": quality,
            }
        )

    return {
        "parameter": parameter,
        "point_id": column.get("id"),
        "name": column.get("name"),
        "aggregate": column.get("aggregate"),
        "interval": column.get("interval"),
        "base_time": column.get("baseTime"),
        "units": column.get("units"),
        "header_record_count": header.get(
            "recordCount"
        ),
        "start_time": header.get(
            "startTime"
        ),
        "end_time": header.get(
            "endTime"
        ),
        "records": output,
    }


# ==============================================================================
# 8. NATIVE REQUEST DISCOVERY
# ==============================================================================

def find_native_request_from_frames(frames):
    for frame in frames:

        parsed = parse_frame(frame)

        if not parsed:
            continue

        obj = parsed["obj"]

        if obj.get("m") != "app_public.getHistoricData":
            continue

        params = obj.get("p")

        if not isinstance(params, list):
            continue

        if not params:
            continue

        request = params[0]

        if not isinstance(request, dict):
            continue

        if request.get(
            "messageType"
        ) != "EXTRACTION_REQUEST_EXTRACT":
            continue

        points = request.get("points")

        if not isinstance(points, list):
            continue

        if not points:
            continue

        point_id = points[0].get("id")

        if point_id in POINT_TO_PARAMETER:
            parameter = POINT_TO_PARAMETER[point_id]
        elif point_id in DEVICE_POINT_TO_PARAMETER:
            parameter = DEVICE_POINT_TO_PARAMETER[point_id]
        elif point_id in WEATHER_POINT_TO_PARAMETER:
            parameter = WEATHER_POINT_TO_PARAMETER[point_id]
        elif point_id in AUX_POINT_TO_PARAMETER:
            parameter = AUX_POINT_TO_PARAMETER[point_id]
        else:
            continue

        return {
            "id": obj.get("id"),
            "uuid": (
                params[1]
                if len(params) > 1
                else None
            ),
            "request": request,
            "point_id": point_id,
            "parameter": parameter,
            "frame": frame,
            "parameter_type": (
                "wqms" if point_id in POINT_TO_PARAMETER
                else "device_health" if point_id in DEVICE_POINT_TO_PARAMETER
                else "weather" if point_id in WEATHER_POINT_TO_PARAMETER
                else "aux"
            ),
        }

    return None


# ==============================================================================
# 9. PLAYWRIGHT WEBSOCKET CAPTURE
# ==============================================================================

async def setup_websocket_capture(page):
    """
    Baseline V4.5.5:
    gunakan Playwright page.on("websocket").

    Ini dipakai sebagai jalur utama untuk capture
    frame send/receive. Tidak mengganti constructor
    WebSocket Eagle.
    """

    sockets = []

    def on_websocket(ws):
        log(
            "WebSocket ditemukan:",
            ws.url
        )

        sockets.append(ws)

        def on_sent(payload):
            if isinstance(payload, str):
                ALL_SENT_FRAMES.append(payload)

        def on_received(payload):
            if isinstance(payload, str):
                ALL_RECEIVED_FRAMES.append(payload)

        try:
            ws.on(
                "framesent",
                on_sent,
            )

            ws.on(
                "framereceived",
                on_received,
            )

        except Exception as exc:
            log(
                "Gagal memasang WebSocket frame listener:",
                exc
            )

    page.on(
        "websocket",
        on_websocket
    )

    return sockets


# ==============================================================================
# 10. WAIT NATIVE REQUEST
# ==============================================================================

async def wait_native_request(
    timeout_ms=NATIVE_WAIT_MS
):
    global NATIVE_REQUEST
    global NATIVE_REQUEST_CAPTURED

    start = asyncio.get_running_loop().time()

    while True:

        result = find_native_request_from_frames(
            ALL_SENT_FRAMES
        )

        if result:

            NATIVE_REQUEST = result
            NATIVE_REQUEST_CAPTURED = True

            log(
                "Native request ditemukan:",
                "id=", result["id"],
                "parameter=", result["parameter"],
                "point=", result["point_id"],
            )

            log(
                "Native startTime:",
                result["request"].get(
                    "startTime"
                )
            )

            log(
                "Native endTime:",
                result["request"].get(
                    "endTime"
                )
            )

            return result

        elapsed = (
            asyncio.get_running_loop().time()
            - start
        ) * 1000

        if elapsed >= timeout_ms:
            return None

        await asyncio.sleep(0.25)


# ==============================================================================
# 11. NATIVE RESPONSE WAIT
# ==============================================================================

async def wait_native_response(
    native_info,
    timeout_ms=45_000
):
    native_uuid = native_info.get("uuid")

    if not native_uuid:
        return None

    start = asyncio.get_running_loop().time()

    while True:

        for frame in ALL_RECEIVED_FRAMES:

            if native_uuid not in frame:
                continue

            wrapper, jts, error = (
                decode_extraction_frame(
                    frame
                )
            )

            if jts is not None:
                return {
                    "frame": frame,
                    "wrapper": wrapper,
                    "jts": jts,
                    "error": error,
                }

        elapsed = (
            asyncio.get_running_loop().time()
            - start
        ) * 1000

        if elapsed >= timeout_ms:
            return None

        await asyncio.sleep(0.25)


# ==============================================================================
# 12. BUILD RAW REQUEST
# ==============================================================================

def build_raw_request(
    native_info,
    custom_id,
    custom_uuid,
    point_id,
):
    """
    Clone EXACT native request.

    Hanya perubahan:
        point ID
        aggregate = NONE

    Dihapus:
        interval
        intervalInclude
        baseTime
    """

    request = json.loads(
        json.dumps(
            native_info["request"]
        )
    )

    points = request.get("points")

    if not points:
        raise ValueError(
            "Native request tidak memiliki points."
        )

    point = json.loads(
        json.dumps(points[0])
    )

    point["id"] = point_id
    point["filterQuality"] = []
    point["renderType"] = "VALUE"
    point["baselineType"] = "ABSOLUTE"

    point["aggregate"] = "NONE"

    point.pop(
        "interval",
        None
    )

    point.pop(
        "intervalInclude",
        None
    )

    point.pop(
        "baseTime",
        None
    )

    request["points"] = [
        point
    ]

    # V7: override CUSTOM extraction range.
    # Native request tetap menjadi template struktur Eagle.
    request["startTime"] = RANGE_START_UTC
    request["endTime"] = RANGE_END_EXCLUSIVE_UTC

    request["format"] = {
        "formatType": "JSON_CHART",
        "qualityEnabled": True,
        "annotationsEnabled": True,
    }

    request["publicId"] = PUBLIC_ID

    frame_obj = {
        "m": "app_public.getHistoricData",
        "id": custom_id,
        "p": [
            request,
            custom_uuid,
        ],
    }

    frame = (
        "42|"
        + json.dumps(
            frame_obj,
            separators=(",", ":"),
            ensure_ascii=False,
        )
    )

    return frame, frame_obj


# ==============================================================================
# 13. SEND RAW
# ==============================================================================

async def send_raw_frame(
    page,
    frame,
):
    """
    Cari WebSocket Playwright yang masih OPEN.

    Untuk mengirim custom frame, kita tetap menggunakan
    native browser WebSocket yang disimpan melalui init script.
    """

    result = await page.evaluate(
        """
        (frame) => {
            const ws = window.__eagleNativeSocket;

            if (!ws) {
                return {
                    ok: false,
                    reason: "native socket tidak ditemukan"
                };
            }

            if (ws.readyState !== WebSocket.OPEN) {
                return {
                    ok: false,
                    reason: "native socket bukan OPEN",
                    readyState: ws.readyState
                };
            }

            try {
                ws.send(frame);

                return {
                    ok: true,
                    readyState: ws.readyState
                };

            } catch (e) {
                return {
                    ok: false,
                    reason: String(e)
                };
            }
        }
        """,
        frame,
    )

    return result


# ==============================================================================
# 14. INIT SCRIPT - HANYA MENYIMPAN SOCKET
# ==============================================================================

INIT_SCRIPT = r"""
(() => {

    if (window.__eagle_v458_baseline) {
        return;
    }

    window.__eagle_v458_baseline = true;
    window.__eagleNativeSocket = null;

    const originalSend =
        WebSocket.prototype.send;

    WebSocket.prototype.send =
        function(data) {

            try {

                if (
                    !window.__eagleNativeSocket &&
                    typeof data === "string" &&
                    data.includes(
                        "app_public.getHistoricData"
                    )
                ) {
                    window.__eagleNativeSocket = this;
                }

            } catch (e) {}

            return originalSend.call(
                this,
                data
            );
        };

})();
"""


# ==============================================================================
# 15. FIND CUSTOM RESPONSE
# ==============================================================================

def find_custom_response(
    custom_uuid
):
    for frame in ALL_RECEIVED_FRAMES:

        if custom_uuid not in frame:
            continue

        wrapper, jts, error = (
            decode_extraction_frame(
                frame
            )
        )

        return {
            "frame": frame,
            "wrapper": wrapper,
            "jts": jts,
            "error": error,
        }

    return None


def has_custom_ack(
    custom_id
):
    for frame in ALL_RECEIVED_FRAMES:

        parsed = parse_frame(frame)

        if not parsed:
            continue

        obj = parsed["obj"]

        if obj.get("id") != custom_id:
            continue

        payload = obj.get("p")

        if (
            isinstance(payload, list)
            and payload
            and payload[0] is True
        ):
            return True

    return False


# ==============================================================================
# 16. REQUEST ONE PARAMETER
# ==============================================================================

async def request_parameter(
    page,
    native_info,
    parameter,
    point_id,
    custom_id,
):
    custom_uuid = str(
        uuid.uuid4()
    )

    frame, frame_obj = (
        build_raw_request(
            native_info=native_info,
            custom_id=custom_id,
            custom_uuid=custom_uuid,
            point_id=point_id,
        )
    )

    section(
        f"RAW {parameter.upper()}"
    )

    log(
        "custom_id :", custom_id
    )

    log(
        "uuid      :", custom_uuid
    )

    log(
        "point_id  :", point_id
    )

    log(
        "aggregate : NONE"
    )

    log(
        "interval  : REMOVED"
    )

    log(
        "baseTime  : REMOVED"
    )

    log(
        "startTime :",
        frame_obj["p"][0]["startTime"]
    )

    log(
        "endTime   :",
        frame_obj["p"][0]["endTime"]
    )

    for attempt in range(
        1,
        MAX_RETRIES + 1
    ):

        log(
            f"Percobaan {attempt}/{MAX_RETRIES}"
        )

        # Snapshot jumlah receive sebelum send.
        receive_before = len(
            ALL_RECEIVED_FRAMES
        )

        send_result = await send_raw_frame(
            page,
            frame,
        )

        log(
            "Send result:",
            send_result
        )

        if not send_result.get("ok"):

            if attempt < MAX_RETRIES:
                await asyncio.sleep(2)
                continue

            return None

        # Tunggu response.
        start = (
            asyncio.get_running_loop().time()
        )

        ack = False
        response = None

        while True:

            if has_custom_ack(
                custom_id
            ):
                ack = True

            response = find_custom_response(
                custom_uuid
            )

            if response:
                break

            elapsed = (
                asyncio.get_running_loop().time()
                - start
            ) * 1000

            if elapsed >= CUSTOM_WAIT_MS:
                break

            await asyncio.sleep(0.25)

        receive_after = len(
            ALL_RECEIVED_FRAMES
        )

        log(
            "Receive frames sebelum:",
            receive_before
        )

        log(
            "Receive frames sesudah:",
            receive_after
        )

        log(
            "Custom ACK   :",
            ack
        )

        log(
            "Custom REPLY :",
            bool(response)
        )

        if response:

            wrapper = response.get(
                "wrapper"
            )

            jts = response.get(
                "jts"
            )

            if jts is None:

                log(
                    "JTS : FALSE"
                )

                if response.get(
                    "error"
                ):
                    log(
                        "Decode error:",
                        response["error"]
                    )

            else:

                log(
                    "JTS : TRUE"
                )

                parsed = parse_jts(
                    jts,
                    parameter,
                )

                log(
                    "Records :",
                    len(
                        parsed["records"]
                    )
                )

                log(
                    "Header count :",
                    parsed[
                        "header_record_count"
                    ]
                )

                log(
                    "Aggregate :",
                    parsed["aggregate"]
                )

                log(
                    "Interval :",
                    parsed["interval"]
                )

                log(
                    "BaseTime :",
                    parsed["base_time"]
                )

                log(
                    "Start :",
                    parsed["start_time"]
                )

                log(
                    "End :",
                    parsed["end_time"]
                )

                if parameter in PARAMETERS:
                    CUSTOM_RESULTS[parameter] = parsed
                elif parameter in DEVICE_HEALTH_PARAMETERS:
                    DEVICE_HEALTH_RESULTS[parameter] = parsed
                elif parameter in WEATHER_PARAMETERS:
                    WEATHER_RESULTS[parameter] = parsed
                elif parameter in AUX_PARAMETERS:
                    AUX_RESULTS[parameter] = parsed

                return parsed

        if attempt < MAX_RETRIES:
            log(
                "Tidak ada extraction reply. Retry."
            )

            await asyncio.sleep(2)

    return None


# ==============================================================================
# 17. NORMALIZE
# ==============================================================================

def normalize_parameter(
    parsed
):
    parameter = parsed[
        "parameter"
    ]

    rows = []

    for record in parsed[
        "records"
    ]:

        value = record[
            "value"
        ]

        if value is None:
            continue

        try:
            value = float(
                value
            )

        except Exception:
            continue

        if not math.isfinite(
            value
        ):
            continue

        rows.append(
            {
                "timestamp_millis":
                    int(
                        record[
                            "timestamp_millis"
                        ]
                    ),
                "timestamp_utc":
                    record[
                        "timestamp_utc"
                    ],
                parameter:
                    value,
            }
        )

    df = pd.DataFrame(
        rows
    )

    if df.empty:
        raise RuntimeError(
            f"{parameter}: tidak ada record valid."
        )

    if df[
        "timestamp_millis"
    ].duplicated().any():

        raise RuntimeError(
            f"{parameter}: duplicate timestamp."
        )

    return df


# ==============================================================================
# 18. MERGE
# ==============================================================================

def build_dataset():
    frames = []

    for parameter in PARAMETERS:

        if parameter not in CUSTOM_RESULTS:
            raise RuntimeError(
                f"{parameter}: response tidak tersedia."
            )

        frames.append(
            normalize_parameter(
                CUSTOM_RESULTS[
                    parameter
                ]
            )
        )

    df = frames[0]

    for other in frames[1:]:

        df = df.merge(
            other,
            on=[
                "timestamp_millis",
                "timestamp_utc",
            ],
            how="outer",
            validate="one_to_one",
        )

    df = df.sort_values(
        "timestamp_millis"
    ).reset_index(
        drop=True
    )

    df[
        "timestamp_utc"
    ] = pd.to_datetime(
        df[
            "timestamp_utc"
        ],
        utc=True,
    )

    df[
        "timestamp_millis"
    ] = df[
        "timestamp_utc"
    ].map(
        lambda x:
            x.value // 1_000_000
    )

    return df[
        EXPECTED_COLUMNS
    ]


# =============================================================================
# 19A. BUILD DEVICE HEALTH DATASET
# =============================================================================

def build_device_health_dataset():
    frames = []

    for parameter in DEVICE_HEALTH_PARAMETERS:
        if parameter not in DEVICE_HEALTH_RESULTS:
            raise RuntimeError(
                f"{parameter}: response device health tidak tersedia."
            )

        frames.append(
            normalize_parameter(
                DEVICE_HEALTH_RESULTS[parameter]
            )
        )

    # Pertahankan timestamp RAW masing-masing point Eagle.
    # Tidak ada interpolasi, fill, atau pemaksaan timestamp WQMS.
    df = frames[0]

    for other in frames[1:]:
        df = df.merge(
            other,
            on=["timestamp_millis", "timestamp_utc"],
            how="outer",
            validate="one_to_one",
        )

    df = df.sort_values(
        "timestamp_millis"
    ).reset_index(drop=True)

    df["timestamp_utc"] = pd.to_datetime(
        df["timestamp_utc"],
        utc=True,
    )

    df["timestamp_millis"] = df["timestamp_utc"].map(
        lambda x: x.value // 1_000_000
    )

    # Struktur tabel/API device health saat ini adalah satu baris berisi
    # empat parameter. Karena itu, jangan pernah mengisi parameter yang
    # timestamp-nya tidak tersedia. Jika timestamp empat point berbeda,
    # hentikan proses dengan jelas daripada membuat data palsu/NaN.
    incomplete = int(
        df[DEVICE_HEALTH_PARAMETERS]
        .isna()
        .any(axis=1)
        .sum()
    )

    if incomplete:
        raise RuntimeError(
            "Timestamp device health antar parameter tidak sejajar: "
            f"{incomplete} baris tidak memiliki keempat parameter. "
            "Tidak ada interpolasi atau pemaksaan timestamp. "
            "Struktur API/database device health saat ini membutuhkan "
            "empat parameter dalam satu timestamp."
        )

    return df[[
        "timestamp_utc",
        "timestamp_millis",
        "battery_voltage",
        "internal_temperature",
        "internal_humidity",
        "current_maximum",
    ]]


# ==============================================================================
# 19B. BUILD WEATHER DATASET
# ==============================================================================

def build_weather_dataset():
    frames = []

    for parameter in WEATHER_PARAMETERS:
        if parameter not in WEATHER_RESULTS:
            raise RuntimeError(
                f"{parameter}: response weather tidak tersedia."
            )

        frames.append(normalize_parameter(WEATHER_RESULTS[parameter]))

    # Weather tidak dipaksa sejajar dengan WQMS/device health.
    # Hanya timestamp yang benar-benar tersedia dari Eagle dipertahankan.
    df = frames[0]

    for other in frames[1:]:
        df = df.merge(
            other,
            on=["timestamp_millis", "timestamp_utc"],
            how="outer",
            validate="one_to_one",
        )

    df = df.sort_values("timestamp_millis").reset_index(drop=True)
    df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True)
    df["timestamp_millis"] = df["timestamp_utc"].map(lambda x: x.value // 1_000_000)

    return df[[
        "timestamp_utc",
        "timestamp_millis",
        "temp_ambient",
        "humidity_ambient",
    ]]


def validate_weather_dataset(df):
    section("VALIDASI WEATHER DATA V7.3")
    errors = []

    expected = [
        "timestamp_utc",
        "timestamp_millis",
        "temp_ambient",
        "humidity_ambient",
    ]

    log("Jumlah baris :", len(df))
    log("Kolom        :", list(df.columns))

    if list(df.columns) != expected:
        errors.append("Kolom weather tidak sesuai.")

    if df.empty:
        errors.append("Dataset weather kosong.")
        log("Weather STATUS : FAIL")
        return "FAIL", errors

    ts = pd.to_datetime(df["timestamp_utc"], utc=True, errors="coerce")
    invalid = int(ts.isna().sum())
    duplicate = int(df["timestamp_millis"].duplicated().sum())
    chronological = bool(ts.is_monotonic_increasing)

    log("Timestamp invalid   :", invalid)
    log("Chronological       :", chronological)
    log("Duplicate timestamp :", duplicate)

    if invalid:
        errors.append("Timestamp weather invalid.")
    if not chronological:
        errors.append("Timestamp weather tidak chronological.")
    if duplicate:
        errors.append("Duplicate timestamp weather.")

    calculated = ts.map(lambda x: x.value // 1_000_000 if pd.notna(x) else -1)
    millis_ok = bool((calculated.to_numpy() == df["timestamp_millis"].to_numpy()).all())
    log("timestamp_millis match :", millis_ok)
    if not millis_ok:
        errors.append("timestamp_millis weather mismatch.")

    # Weather tidak dipaksa memiliki timestamp yang sama antar-parameter.
    # Karena itu NULL akibat outer-merge bukan dianggap error. Yang wajib adalah
    # setiap point memiliki record validnya sendiri dan tidak ada nilai Infinity.
    for parameter in WEATHER_PARAMETERS:
        numeric = pd.to_numeric(df[parameter], errors="coerce")
        valid = int(numeric.notna().sum())
        null = int(numeric.isna().sum())
        infinity = int(np.isinf(numeric.fillna(0).to_numpy()).sum())
        log(f"{parameter:20s} valid={valid} NULL={null} Infinity={infinity}")
        if valid == 0:
            errors.append(f"{parameter}: tidak ada record valid.")
        if infinity:
            errors.append(f"{parameter}: Infinity.")

    status = "PASS" if not errors else "FAIL"
    log("Interpolasi      : TIDAK")
    log("Smoothing        : TIDAK")
    log("Pengisian gap    : TIDAK")
    log("Pembulatan waktu : TIDAK")
    log("Perubahan nilai  : TIDAK")
    log("Timestamp sumber : EAGLE")
    log("Weather STATUS   :", status)

    for error in errors:
        log("ERROR:", error)

    return status, errors


# ==============================================================================
# 19C. BUILD AUXILIARY DATASET - PRESSURE + DEPTH
# ==============================================================================

def build_aux_dataset():
    frames = []

    for parameter in AUX_PARAMETERS:
        if parameter not in AUX_RESULTS:
            raise RuntimeError(
                f"{parameter}: response auxiliary tidak tersedia."
            )
        frames.append(normalize_parameter(AUX_RESULTS[parameter]))

    df = frames[0]

    for other in frames[1:]:
        df = df.merge(
            other,
            on=["timestamp_millis", "timestamp_utc"],
            how="outer",
            validate="one_to_one",
        )

    df = df.sort_values("timestamp_millis").reset_index(drop=True)
    df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True)
    df["timestamp_millis"] = df["timestamp_utc"].map(lambda x: x.value // 1_000_000)

    return df[["timestamp_utc", "timestamp_millis", "barometric_pressure", "depth"]]


def validate_aux_dataset(df):
    section("VALIDASI PRESSURE + DEPTH V7.4")
    errors = []
    expected = ["timestamp_utc", "timestamp_millis", "barometric_pressure", "depth"]

    log("Jumlah baris :", len(df))
    log("Kolom        :", list(df.columns))

    if list(df.columns) != expected:
        errors.append("Kolom pressure/depth tidak sesuai.")
    if df.empty:
        errors.append("Dataset pressure/depth kosong.")
        log("Pressure + Depth STATUS : FAIL")
        return "FAIL", errors

    ts = pd.to_datetime(df["timestamp_utc"], utc=True, errors="coerce")
    invalid = int(ts.isna().sum())
    duplicate = int(df["timestamp_millis"].duplicated().sum())
    chronological = bool(ts.is_monotonic_increasing)
    log("Timestamp invalid   :", invalid)
    log("Chronological       :", chronological)
    log("Duplicate timestamp :", duplicate)

    if invalid: errors.append("Timestamp auxiliary invalid.")
    if not chronological: errors.append("Timestamp auxiliary tidak chronological.")
    if duplicate: errors.append("Duplicate timestamp auxiliary.")

    calculated = ts.map(lambda x: x.value // 1_000_000 if pd.notna(x) else -1)
    millis_ok = bool((calculated.to_numpy() == df["timestamp_millis"].to_numpy()).all())
    log("timestamp_millis match :", millis_ok)
    if not millis_ok: errors.append("timestamp_millis auxiliary mismatch.")

    # Pressure memang lebih jarang daripada Depth. Tidak dipaksa sejajar.
    for parameter in AUX_PARAMETERS:
        numeric = pd.to_numeric(df[parameter], errors="coerce")
        valid = int(numeric.notna().sum())
        null = int(numeric.isna().sum())
        infinity = int(np.isinf(numeric.fillna(0).to_numpy()).sum())
        log(f"{parameter:20s} valid={valid} NULL={null} Infinity={infinity}")
        if valid == 0: errors.append(f"{parameter}: tidak ada record valid.")
        if infinity: errors.append(f"{parameter}: Infinity.")
        if valid:
            log(f"{parameter:20s} min={numeric.min():.6f} max={numeric.max():.6f} mean={numeric.mean():.6f} median={numeric.median():.6f}")

    log("Interpolasi      : TIDAK")
    log("Smoothing        : TIDAK")
    log("Pengisian gap    : TIDAK")
    log("Pembulatan waktu : TIDAK")
    log("Perubahan nilai  : TIDAK")
    log("Timestamp sumber : EAGLE")

    status = "PASS" if not errors else "FAIL"
    log("Pressure + Depth STATUS :", status)
    for error in errors: log("ERROR:", error)
    return status, errors


# ==============================================================================
# 19. VALIDATION historical reference STYLE
# ==============================================================================

def validate_dataset(
    df
):
    section(
        "VALIDASI FINAL DATASET V7.2"
    )

    errors = []

    log(
        "[1] Jumlah baris :",
        len(df)
    )

    if len(df) == 0:
        errors.append(
            "Dataset kosong."
        )

    log(
        "[2] Kolom :",
        list(df.columns)
    )

    if list(df.columns) != EXPECTED_COLUMNS:
        errors.append(
            "Kolom tidak sesuai."
        )

    timestamp = pd.to_datetime(
        df[
            "timestamp_utc"
        ],
        utc=True,
        errors="coerce",
    )

    invalid_ts = int(
        timestamp.isna().sum()
    )

    log(
        "[3] Timestamp invalid :",
        invalid_ts
    )

    if invalid_ts:
        errors.append(
            "Timestamp invalid."
        )

    chronological = (
        timestamp.is_monotonic_increasing
    )

    log(
        "[4] Chronological :",
        chronological
    )

    if not chronological:
        errors.append(
            "Timestamp tidak chronological."
        )

    duplicate = int(
        df[
            "timestamp_millis"
        ].duplicated().sum()
    )

    log(
        "[5] Duplicate timestamp :",
        duplicate
    )

    if duplicate:
        errors.append(
            "Duplicate timestamp."
        )

    calculated = timestamp.map(
        lambda x:
            x.value // 1_000_000
    )

    millis_ok = (
        calculated.to_numpy()
        ==
        df[
            "timestamp_millis"
        ].to_numpy()
    ).all()

    log(
        "[6] timestamp_millis match :",
        millis_ok
    )

    if not millis_ok:
        errors.append(
            "timestamp_millis mismatch."
        )

    section(
        "VALIDASI 7 PARAMETER"
    )

    for parameter in PARAMETERS:

        numeric = pd.to_numeric(
            df[
                parameter
            ],
            errors="coerce",
        )

        valid = int(
            numeric.notna().sum()
        )

        null = int(
            numeric.isna().sum()
        )

        infinity = int(
            np.isinf(
                numeric.fillna(
                    0
                ).to_numpy()
            ).sum()
        )

        log(
            f"{parameter:15s} "
            f"valid={valid} "
            f"NULL={null} "
            f"Infinity={infinity}"
        )

        if null:
            errors.append(
                f"{parameter}: NULL."
            )

        if infinity:
            errors.append(
                f"{parameter}: Infinity."
            )

    incomplete = int(
        df[
            PARAMETERS
        ].isna().any(
            axis=1
        ).sum()
    )

    log(
        "Timestamp tidak lengkap :",
        incomplete
    )

    if incomplete:
        errors.append(
            f"Ada {incomplete} timestamp "
            "tidak lengkap."
        )

    if len(df) >= 2:

        delta = (
            df[
                "timestamp_millis"
            ].diff()
            .dropna()
            / 1000
        )

        unique = sorted(
            set(
                round(
                    float(x),
                    3
                )
                for x in delta
            )
        )

        log(
            "Delta unik :",
            unique
        )

        gap = int(
            (delta > 900).sum()
        )

        log(
            "Gap > 15 menit :",
            gap
        )

    section(
        "STATISTIK"
    )

    for parameter in PARAMETERS:

        values = pd.to_numeric(
            df[
                parameter
            ],
            errors="coerce",
        )

        log(
            f"{parameter:15s} "
            f"min={values.min():.6f} "
            f"max={values.max():.6f} "
            f"mean={values.mean():.6f} "
            f"median={values.median():.6f}"
        )

    section(
        "INTEGRITAS"
    )

    log(
        "Interpolasi      : TIDAK"
    )
    log(
        "Smoothing        : TIDAK"
    )
    log(
        "Pengisian gap    : TIDAK"
    )
    log(
        "Pembulatan waktu : TIDAK"
    )
    log(
        "Perubahan nilai  : TIDAK"
    )
    log(
        "Timestamp sumber : EAGLE"
    )

    status = (
        "PASS"
        if not errors
        else "FAIL"
    )

    section(
        "KESIMPULAN VALIDASI"
    )

    log(
        "STATUS :",
        status
    )

    for error in errors:
        log(
            "ERROR:",
            error
        )

    return status, errors


# ==============================================================================
# 21. SAVE RAW TRAFFIC
# ==============================================================================

def save_raw_traffic():
    # Raw WebSocket traffic is kept out of persistent storage in GitHub mode.
    return None


# ==============================================================================
# 22. API POST V4.5.9
# ==============================================================================

def post_wqms_to_api(df):
    section("V7.4 - POST WQMS KE API")

    if df.empty:
        raise RuntimeError("Dataset WQMS kosong.")

    send_df = (
        df.copy()
        if API_SEND_ALL
        else df.head(API_TEST_LIMIT).copy()
    )

    log("API URL        :", API_URL)
    log("API_SEND_ALL   :", API_SEND_ALL)
    log("API_TEST_LIMIT :", API_TEST_LIMIT, "(diabaikan jika API_SEND_ALL=True)")
    log("Jumlah record  :", len(send_df))

    results = []

    for number, (_, row) in enumerate(send_df.iterrows(), 1):
        ts = pd.to_datetime(row["timestamp_utc"], utc=True)
        timestamp_api = ts.strftime("%Y-%m-%d %H:%M:%S")

        payload = {
            "wq_ms": {
                "bga_pc": float(row["bga_pc"]),
                "chlorophyll": float(row["chlorophyll"]),
                "external_temp": float(row["external_temp"]),
                "odo_sat": float(row["odo_sat"]),
                "salinity": float(row["salinity"]),
                "turbidity": float(row["turbidity"]),
                "fdom": float(row["fdom"]),
                "status": "OK",
                "timestamp": timestamp_api,
            }
        }

        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

        log("-" * 70)
        log("POST record      :", number)
        log("timestamp_millis :", int(row["timestamp_millis"]))
        log("timestamp Eagle  :", ts.isoformat())
        log("timestamp API    :", timestamp_api)
        log("Payload          :", body.decode("utf-8"))

        request = Request(
            API_URL,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json,text/plain,*/*",
                "User-Agent": "Eagle-WQMS-Scraper/4.5.9",
            },
        )

        http_status = None
        response_text = ""
        success = False

        try:
            with urlopen(
                request,
                timeout=API_TIMEOUT_SECONDS,
            ) as response:
                http_status = response.getcode()
                response_text = response.read().decode(
                    "utf-8",
                    errors="replace",
                ).strip()

        except HTTPError as exc:
            http_status = exc.code
            try:
                response_text = exc.read().decode(
                    "utf-8",
                    errors="replace",
                ).strip()
            except Exception:
                response_text = str(exc)

        except URLError as exc:
            response_text = f"URLError: {exc.reason}"

        except Exception as exc:
            response_text = f"{type(exc).__name__}: {exc}"

        success = (
            http_status is not None
            and 200 <= http_status < 300
            and (
                "SUCCESS: Data berhasil disimpan" in response_text
                or "DUPLICATE: Timestamp sudah ada" in response_text
                or "INSERT: Data berhasil disimpan" in response_text
                or "UPDATE: Data berhasil diperbarui" in response_text
            )
        )

        log("HTTP status     :", http_status)
        log("Response API    :", response_text)
        log("HASIL           :", "SUCCESS" if success else "FAILED")

        results.append({
            "record": number,
            "timestamp_millis": int(row["timestamp_millis"]),
            "timestamp_api": timestamp_api,
            "http_status": http_status,
            "response": response_text,
            "success": success,
        })

    attempted = len(results)
    success_count = sum(1 for x in results if x["success"])
    failed_count = attempted - success_count

    section("HASIL POST API V7.4")
    log("Attempted :", attempted)
    log("Success   :", success_count)
    log("Failed    :", failed_count)

    if failed_count:
        raise RuntimeError(
            f"API POST gagal: {failed_count}/{attempted} record."
        )

    return {
        "attempted": attempted,
        "success": success_count,
        "failed": failed_count,
    }


# =============================================================================
# 22A. API POST WQMS + WEATHER + PRESSURE + DEPTH
# =============================================================================

def post_combined_wqms_to_api(df, weather_df, aux_df):
    section("V7.4 - POST 11 PARAMETER KE API")

    if df.empty:
        raise RuntimeError("Dataset WQMS kosong.")
    if weather_df.empty:
        raise RuntimeError("Dataset Weather kosong.")
    if aux_df.empty:
        raise RuntimeError("Dataset Pressure/Depth kosong.")

    send_df = df.copy() if API_SEND_ALL else df.head(API_TEST_LIMIT).copy()

    # Lookup berdasarkan timestamp_millis.
    weather_lookup = weather_df.set_index("timestamp_millis").to_dict("index")
    aux_lookup = aux_df.set_index("timestamp_millis").to_dict("index")

    log("API URL        :", API_URL)
    log("API_SEND_ALL   :", API_SEND_ALL)
    log("API_TEST_LIMIT :", API_TEST_LIMIT, "(diabaikan jika API_SEND_ALL=True)")
    log("Jumlah WQMS record POST :", len(send_df))
    log("Payload        : 7 WQMS + 2 Weather + Pressure + Depth")
    log("Timestamp API  : UTC sumber Eagle")

    results = []

    for number, (_, row) in enumerate(send_df.iterrows(), 1):
        timestamp_millis = int(row["timestamp_millis"])
        ts = pd.to_datetime(row["timestamp_utc"], utc=True)
        timestamp_api = ts.strftime("%Y-%m-%d %H:%M:%S")

        weather = weather_lookup.get(timestamp_millis, {})
        aux = aux_lookup.get(timestamp_millis, {})

        def nullable_float(value):
            if value is None:
                return None
            try:
                value = float(value)
            except (TypeError, ValueError):
                return None
            if not math.isfinite(value):
                return None
            return value

        temp_ambient = nullable_float(weather.get("temp_ambient"))
        humidity_ambient = nullable_float(weather.get("humidity_ambient"))
        barometric_pressure = nullable_float(aux.get("barometric_pressure"))
        depth = nullable_float(aux.get("depth"))

        payload = {
            "wq_ms": {
                "bga_pc": float(row["bga_pc"]),
                "chlorophyll": float(row["chlorophyll"]),
                "external_temp": float(row["external_temp"]),
                "odo_sat": float(row["odo_sat"]),
                "salinity": float(row["salinity"]),
                "turbidity": float(row["turbidity"]),
                "fdom": float(row["fdom"]),
                "temp_ambient": temp_ambient,
                "humidity_ambient": humidity_ambient,
                "barometric_pressure": barometric_pressure,
                "depth": depth,
                "status": "OK",
                "timestamp": timestamp_api,
            }
        }

        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

        log("-" * 70)
        log("POST record      :", number, "/", len(send_df))
        log("timestamp_millis :", timestamp_millis)
        log("timestamp Eagle  :", ts.isoformat())
        log("timestamp API    :", timestamp_api)
        log("temp_ambient     :", temp_ambient)
        log("humidity_ambient :", humidity_ambient)
        log("barometric_pressure :", barometric_pressure)
        log("depth            :", depth)

        request = Request(
            API_URL,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json,text/plain,*/*",
                "User-Agent": "Eagle-WQMS-Scraper/7.4-API",
            },
        )

        http_status = None
        response_text = ""

        try:
            with urlopen(request, timeout=API_TIMEOUT_SECONDS) as response:
                http_status = response.getcode()
                response_text = response.read().decode("utf-8", errors="replace").strip()
        except HTTPError as exc:
            http_status = exc.code
            try:
                response_text = exc.read().decode("utf-8", errors="replace").strip()
            except Exception:
                response_text = str(exc)
        except URLError as exc:
            response_text = f"URLError: {exc.reason}"
        except Exception as exc:
            response_text = f"{type(exc).__name__}: {exc}"

        success = (
            http_status is not None
            and 200 <= http_status < 300
            and (
                "SUCCESS: Data berhasil disimpan" in response_text
                or "DUPLICATE: Timestamp sudah ada" in response_text
                or "INSERT: Data berhasil disimpan" in response_text
                or "UPDATE: Data berhasil diperbarui" in response_text
            )
        )

        log("HTTP status     :", http_status)
        log("Response API    :", response_text)
        log("HASIL           :", "SUCCESS" if success else "FAILED")

        results.append({
            "record": number,
            "timestamp_millis": timestamp_millis,
            "timestamp_api": timestamp_api,
            "http_status": http_status,
            "response": response_text,
            "success": success,
        })

    attempted = len(results)
    success_count = sum(1 for x in results if x["success"])
    failed_count = attempted - success_count

    section("HASIL POST 11 PARAMETER")
    log("Attempted :", attempted)
    log("Success   :", success_count)
    log("Failed    :", failed_count)

    if failed_count:
        raise RuntimeError(
            f"API POST 11 parameter gagal: {failed_count}/{attempted} record."
        )

    return {
        "attempted": attempted,
        "success": success_count,
        "failed": failed_count,
    }


# =============================================================================
# 22B. API POST DEVICE HEALTH
# =============================================================================

def post_device_health_to_api(df):
    section("V7.4 - POST DEVICE HEALTH KE API")

    if df.empty:
        raise RuntimeError("Dataset device health kosong.")

    send_df = df.copy() if API_SEND_ALL else df.head(API_TEST_LIMIT).copy()
    log("API URL        :", API_URL)
    log("Jumlah record  :", len(send_df))

    results = []

    for number, (_, row) in enumerate(send_df.iterrows(), 1):
        ts = pd.to_datetime(row["timestamp_utc"], utc=True)
        timestamp_api = ts.strftime("%Y-%m-%d %H:%M:%S")

        payload = {
            "device_health": {
                "battery_voltage": float(row["battery_voltage"]),
                "current_maximum": float(row["current_maximum"]),
                "internal_humidity": float(row["internal_humidity"]),
                "internal_temperature": float(row["internal_temperature"]),
                "status": "OK",
                "timestamp": timestamp_api,
            }
        }

        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

        log("-" * 70)
        log("POST device health record :", number)
        log("timestamp_millis :", int(row["timestamp_millis"]))
        log("timestamp Eagle  :", ts.isoformat())
        log("timestamp API    :", timestamp_api)

        request = Request(
            API_URL,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json,text/plain,*/*",
                "User-Agent": "Eagle-WQMS-Scraper/7.2",
            },
        )

        http_status = None
        response_text = ""

        try:
            with urlopen(request, timeout=API_TIMEOUT_SECONDS) as response:
                http_status = response.getcode()
                response_text = response.read().decode("utf-8", errors="replace").strip()
        except HTTPError as exc:
            http_status = exc.code
            try:
                response_text = exc.read().decode("utf-8", errors="replace").strip()
            except Exception:
                response_text = str(exc)
        except URLError as exc:
            response_text = f"URLError: {exc.reason}"
        except Exception as exc:
            response_text = f"{type(exc).__name__}: {exc}"

        success = (
            http_status is not None
            and 200 <= http_status < 300
            and (
                "SUCCESS: Data berhasil disimpan" in response_text
                or "DUPLICATE: Timestamp sudah ada" in response_text
                or "INSERT: Data berhasil disimpan" in response_text
                or "UPDATE: Data berhasil diperbarui" in response_text
            )
        )

        log("HTTP status     :", http_status)
        log("Response API    :", response_text)
        log("HASIL           :", "SUCCESS" if success else "FAILED")

        results.append({
            "record": number,
            "timestamp_millis": int(row["timestamp_millis"]),
            "timestamp_api": timestamp_api,
            "http_status": http_status,
            "response": response_text,
            "success": success,
        })

    attempted = len(results)
    success_count = sum(1 for x in results if x["success"])
    failed_count = attempted - success_count

    section("HASIL POST DEVICE HEALTH V7.4")
    log("Attempted :", attempted)
    log("Success   :", success_count)
    log("Failed    :", failed_count)

    if failed_count:
        raise RuntimeError(f"API Device Health gagal: {failed_count}/{attempted} record.")

    return {"attempted": attempted, "success": success_count, "failed": failed_count}


# ==============================================================================
# 23. MAIN
# ==============================================================================

async def main():

    global CURRENT_PAGE

    section(
        "EAGLE V7.4 - WQMS + DEVICE HEALTH + WEATHER + PRESSURE + DEPTH"
    )

    log(
        "TEST_MODE       :",
        TEST_MODE
    )

    log(
        "API_POST        :",
        API_POST
    )

    log(
        "DATABASE_INSERT :",
        DATABASE_INSERT
    )

    log(
        "RANGE LOCAL     :",
        RANGE_START_LOCAL,
        "s/d",
        RANGE_END_EXCLUSIVE_LOCAL,
        "(END exclusive)"
    )

    log(
        "RANGE UTC       :",
        RANGE_START_UTC,
        "s/d",
        RANGE_END_EXCLUSIVE_UTC,
        "(END exclusive)"
    )

    if not TEST_MODE:
        raise RuntimeError(
            "TEST_MODE harus True."
        )

    if DATABASE_INSERT:
        raise RuntimeError(
            "DATABASE_INSERT harus False."
        )

    browser = None
    context = None
    page = None

    try:

        section(
            "MEMBUKA BROWSER"
        )

        async with async_playwright() as p:

            browser = await p.chromium.launch(
                headless=HEADLESS
            )

            context = await browser.new_context(
                timezone_id=TIMEZONE_ID,
                viewport={
                    "width": 1440,
                    "height": 900,
                },
            )

            page = await context.new_page()

            CURRENT_PAGE = page

            # Init script dipasang SEBELUM goto.
            await page.add_init_script(
                INIT_SCRIPT
            )

            # Playwright WS listener dipasang SEBELUM goto.
            await setup_websocket_capture(
                page
            )

            log(
                "Membuka Eagle:",
                EAGLE_URL
            )

            await page.goto(
                EAGLE_URL,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT_MS,
            )

            log(
                "Halaman Eagle berhasil dibuka."
            )

            try:

                await page.wait_for_load_state(
                    "networkidle",
                    timeout=30_000
                )

            except PlaywrightTimeoutError:

                log(
                    "networkidle timeout - "
                    "lanjut."
                )

            log(
                "Menunggu dashboard memuat..."
            )

            await page.wait_for_timeout(
                DASHBOARD_WAIT_MS
            )

            # ------------------------------------------------------------------
            # Native request
            # ------------------------------------------------------------------

            section(
                "MENUNGGU NATIVE HISTORIC REQUEST"
            )

            native = await wait_native_request()

            if native is None:
                raise RuntimeError(
                    "Native historic request tidak ditemukan."
                )

            # ------------------------------------------------------------------
            # Native response
            # ------------------------------------------------------------------

            section(
                "MENUNGGU NATIVE RESPONSE"
            )

            native_response = (
                await wait_native_response(
                    native
                )
            )

            log(
                "Native response :",
                bool(native_response)
            )

            if native_response:

                log(
                    "Native response JTS :",
                    native_response[
                        "jts"
                    ] is not None
                )

            else:

                log(
                    "Native response tidak "
                    "terlihat melalui Playwright frame listener."
                )

            # Kita TIDAK menghentikan proses hanya karena
            # native response tidak tertangkap. Yang penting
            # native request sudah diketahui dan socket browser
            # akan digunakan untuk custom request.

            # ------------------------------------------------------------------
            # Custom 7 parameters
            # ------------------------------------------------------------------

            custom_id = CUSTOM_ID_START

            for parameter in PARAMETERS:

                point_id = TARGET_POINTS[
                    parameter
                ]

                result = await request_parameter(
                    page=page,
                    native_info=native,
                    parameter=parameter,
                    point_id=point_id,
                    custom_id=custom_id,
                )

                if result is None:

                    raise RuntimeError(
                        f"Pengambilan {parameter} gagal."
                    )

                custom_id += 1

                await page.wait_for_timeout(
                    REQUEST_DELAY_MS
                )

            # ------------------------------------------------------------------
            # Custom 4 parameter DEVICE HEALTH
            # ------------------------------------------------------------------

            section("MENGAMBIL DATA DEVICE HEALTH")

            for parameter in DEVICE_HEALTH_PARAMETERS:
                point_id = DEVICE_HEALTH_POINTS[parameter]

                result = await request_parameter(
                    page=page,
                    native_info=native,
                    parameter=parameter,
                    point_id=point_id,
                    custom_id=custom_id,
                )

                if result is None:
                    raise RuntimeError(
                        f"Pengambilan device health {parameter} gagal."
                    )

                custom_id += 1

                await page.wait_for_timeout(REQUEST_DELAY_MS)

            # ------------------------------------------------------------------
            # Custom 2 parameter WEATHER
            # ------------------------------------------------------------------

            section("MENGAMBIL DATA WEATHER")

            for parameter in WEATHER_PARAMETERS:
                point_id = WEATHER_POINTS[parameter]

                result = await request_parameter(
                    page=page,
                    native_info=native,
                    parameter=parameter,
                    point_id=point_id,
                    custom_id=custom_id,
                )

                if result is None:
                    raise RuntimeError(
                        f"Pengambilan weather {parameter} gagal."
                    )

                custom_id += 1
                await page.wait_for_timeout(REQUEST_DELAY_MS)

            # ------------------------------------------------------------------
            # Custom PRESSURE + DEPTH
            # ------------------------------------------------------------------

            section("MENGAMBIL DATA PRESSURE + DEPTH")

            for parameter in AUX_PARAMETERS:
                point_id = PRESSURE_POINTS[parameter] if parameter in PRESSURE_POINTS else DEPTH_POINTS[parameter]

                result = await request_parameter(
                    page=page,
                    native_info=native,
                    parameter=parameter,
                    point_id=point_id,
                    custom_id=custom_id,
                )

                if result is None:
                    raise RuntimeError(
                        f"Pengambilan pressure/depth {parameter} gagal."
                    )

                custom_id += 1
                await page.wait_for_timeout(REQUEST_DELAY_MS)

            # ------------------------------------------------------------------
            # Dataset
            # ------------------------------------------------------------------

            section(
                "MEMBANGUN DATASET"
            )

            df = build_dataset()

            # Eagle dapat mengembalikan record tepat pada END boundary.
            # Karena END request bersifat exclusive, record >= END harus
            # dibuang. Record < START juga dibuang sebagai perlindungan.
            range_start_dt = pd.Timestamp(RANGE_START_UTC)
            range_end_dt = pd.Timestamp(RANGE_END_EXCLUSIVE_UTC)

            outside = (
                (df["timestamp_utc"] < range_start_dt)
                | (df["timestamp_utc"] >= range_end_dt)
            )

            outside_count = int(outside.sum())

            log(
                "Record di luar range sebelum filter :",
                outside_count
            )

            if outside_count:
                log(
                    "Eagle mengembalikan boundary record; "
                    "record di luar range akan dibuang."
                )

                df = df.loc[
                    ~outside
                ].copy().reset_index(
                    drop=True
                )

            # Setelah filter, harus benar-benar tidak ada record di luar range.
            outside_after = (
                (df["timestamp_utc"] < range_start_dt)
                | (df["timestamp_utc"] >= range_end_dt)
            )

            outside_after_count = int(
                outside_after.sum()
            )

            log(
                "Record di luar range sesudah filter :",
                outside_after_count
            )

            if outside_after_count:
                raise RuntimeError(
                    f"Masih ada {outside_after_count} record di luar range V7."
                )

            log(
                "Jumlah record final :",
                len(df)
            )

            log(
                "Kolom :",
                list(df.columns)
            )

            if len(df):

                log(
                    "Pertama :",
                    df[
                        "timestamp_utc"
                    ].iloc[0]
                )

                log(
                    "Terakhir :",
                    df[
                        "timestamp_utc"
                    ].iloc[-1]
                )

            # ------------------------------------------------------------------
            # Validation
            # ------------------------------------------------------------------

            validation = (
                validate_dataset(
                    df
                )
            )

            # ------------------------------------------------------------------
            # WEATHER DATASET + VALIDATION
            # ------------------------------------------------------------------

            weather_df = build_weather_dataset()

            weather_start_dt = pd.Timestamp(RANGE_START_UTC)
            weather_end_dt = pd.Timestamp(RANGE_END_EXCLUSIVE_UTC)
            weather_outside = (
                (weather_df["timestamp_utc"] < weather_start_dt)
                | (weather_df["timestamp_utc"] >= weather_end_dt)
            )
            weather_outside_count = int(weather_outside.sum())
            log("Weather record di luar range sebelum filter :", weather_outside_count)

            if weather_outside_count:
                weather_df = weather_df.loc[~weather_outside].copy().reset_index(drop=True)

            weather_validation = validate_weather_dataset(weather_df)

            log("Weather record final :", len(weather_df))
            if len(weather_df):
                log("Weather pertama :", weather_df["timestamp_utc"].iloc[0])
                log("Weather terakhir :", weather_df["timestamp_utc"].iloc[-1])

            # ------------------------------------------------------------------
            # PRESSURE + DEPTH DATASET + VALIDATION
            # ------------------------------------------------------------------

            aux_df = build_aux_dataset()
            aux_start_dt = pd.Timestamp(RANGE_START_UTC)
            aux_end_dt = pd.Timestamp(RANGE_END_EXCLUSIVE_UTC)
            aux_outside = (
                (aux_df["timestamp_utc"] < aux_start_dt)
                | (aux_df["timestamp_utc"] >= aux_end_dt)
            )
            aux_outside_count = int(aux_outside.sum())
            log("Pressure/Depth record di luar range sebelum filter :", aux_outside_count)

            if aux_outside_count:
                aux_df = aux_df.loc[~aux_outside].copy().reset_index(drop=True)

            aux_validation = validate_aux_dataset(aux_df)
            log("Pressure/Depth record final :", len(aux_df))

            # ------------------------------------------------------------------
            # API POST
            # API existing tetap hanya untuk WQMS + device health.
            # Weather belum dikirim ke API pada V7.3 agar endpoint PHP yang
            # sudah PASS tidak diubah sebelum struktur tabel/API weather disepakati.
            # ------------------------------------------------------------------

            api_result = None
            device_health_api_result = None

            if API_POST:

                if validation[0] != "PASS":
                    raise RuntimeError(
                        "API POST dibatalkan karena dataset WQMS tidak lolos validasi."
                    )

                if weather_validation[0] != "PASS":
                    raise RuntimeError(
                        "API POST dibatalkan karena dataset weather tidak lolos validasi."
                    )

                api_result = post_combined_wqms_to_api(
                    df,
                    weather_df,
                    aux_df,
                )

                device_health_df = build_device_health_dataset()

                log(
                    "Jumlah record device health :",
                    len(device_health_df)
                )

                device_health_api_result = post_device_health_to_api(
                    device_health_df
                )

            section(
                "KESIMPULAN EAGLE V7.4 RANGE API TEST"
            )

            api_status_ok = (
                not API_POST
                or (
                    api_result is not None
                    and api_result["failed"] == 0
                    and device_health_api_result is not None
                    and device_health_api_result["failed"] == 0
                )
            )

            overall_status = (
                "PASS"
                if validation[0] == "PASS"
                and weather_validation[0] == "PASS"
                and aux_validation[0] == "PASS"
                and api_status_ok
                else "FAIL"
            )

            log(
                "STATUS :",
                overall_status
            )

            log(
                "Total record :",
                len(df)
            )

            log(
                "Parameter WQMS :",
                len(PARAMETERS)
            )

            log(
                "Parameter Device Health :",
                len(DEVICE_HEALTH_PARAMETERS)
            )

            log(
                "Parameter Weather :",
                len(WEATHER_PARAMETERS)
            )

            log(
                "Parameter Pressure + Depth :",
                len(AUX_PARAMETERS)
            )

            log(
                "Weather record :",
                len(weather_df)
            )

            log(
                "Weather validation :",
                weather_validation[0]
            )

            log(
                "Pressure/Depth record :",
                len(aux_df)
            )

            log(
                "Pressure/Depth validation :",
                aux_validation[0]
            )

            log(
                "API POST :",
                API_POST
            )

            log(
                "Database INSERT :",
                DATABASE_INSERT
            )

            if api_result is not None:
                log(
                    "API attempted   :",
                    api_result["attempted"]
                )
                log(
                    "API success     :",
                    api_result["success"]
                )
                log(
                    "API failed      :",
                    api_result["failed"]
                )

            if device_health_api_result is not None:
                log(
                    "Logger API attempted :",
                    device_health_api_result["attempted"]
                )
                log(
                    "Logger API success   :",
                    device_health_api_result["success"]
                )
                log(
                    "Logger API failed    :",
                    device_health_api_result["failed"]
                )

    except Exception as exc:

        section(
            "ERROR V7.4"
        )

        log(
            type(exc).__name__,
            ":",
            exc
        )

        log("Traceback:")
        log(traceback.format_exc())
        raise

    finally:

        try:

            if context:
                await context.close()

        except Exception:
            pass

        try:

            if browser:
                await browser.close()

        except Exception:
            pass


# ==============================================================================
# 24. WINDOWS ENTRY POINT
# ==============================================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        print(
            "\nProgram dihentikan user.",
            flush=True
        )

    except Exception as exc:

        print(
            "\nFATAL:",
            type(exc).__name__,
            exc,
            flush=True
        )
        raise SystemExit(1)

    finally:

        print(
            "\n" + "=" * 78
        )

        print(
            "EAGLE V7.4 RANGE API TEST SELESAI."
        )

        print(
            "=" * 78
        )
        print("Output file permanen: TIDAK ADA (GitHub Actions).")

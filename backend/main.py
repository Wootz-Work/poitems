from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import cv2
import numpy as np
import os
import asyncio
import logging
import re
import httpx  # used to fecth drawing numbers (File name)
import uuid
from fpdf import FPDF
import requests
import tempfile
import json
import cloudinary
import cloudinary.uploader
import po_assemblies


# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

print("🚀 Server has started and main.py is loaded")

# Configure CORS - explicitly allow your GitHub Pages d
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://veenu-wootz.github.io", "https://wootz-work.github.io", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "HEAD", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(po_assemblies.router)

# Paths to pre-downloaded model files
PADDLE_HOME = os.path.expanduser("~/.paddleocr")
DET_MODEL_DIR = os.path.join(PADDLE_HOME, "whl/det/en/en_PP-OCRv3_det_infer")
REC_MODEL_DIR = os.path.join(PADDLE_HOME, "whl/rec/en/en_PP-OCRv3_rec_infer")
CLS_MODEL_DIR = os.path.join(PADDLE_HOME, "whl/cls/ch_ppocr_mobile_v2.0_cls_infer")

# Check and log model paths on startup
@app.on_event("startup")
async def startup_event():
    logger.info(f"Starting OCR API with model paths:")
    logger.info(f"Detection model: {DET_MODEL_DIR} (exists: {os.path.exists(DET_MODEL_DIR)})")
    logger.info(f"Recognition model: {REC_MODEL_DIR} (exists: {os.path.exists(REC_MODEL_DIR)})")
    logger.info(f"Classification model: {CLS_MODEL_DIR} (exists: {os.path.exists(CLS_MODEL_DIR)})")

# Code look for an “O” preceded by whitespace and followed by a digit and replaces it with “Ø”
def fix_diameter(text: str) -> str:
    # look for an “O” preceded by whitespace and followed by a digit,
    # and replace it with “Ø”
    return re.sub(r'(?<=\s)O(?=\d)', 'Ø', text)

# Initialize OCR model
def get_ocr_model():
    from paddleocr import PaddleOCR
    logger.info("Initializing PaddleOCR model with pre-downloaded model files")
    return PaddleOCR(
        use_angle_cls=True,
        lang='en',
        det_model_dir=DET_MODEL_DIR,
        rec_model_dir=REC_MODEL_DIR,
        cls_model_dir=CLS_MODEL_DIR,
        use_gpu=False
    )

# Process image with OCR
def simple_cells(img_rgb):
    """
    Run PaddleOCR on an RGB image and return one cell per detected box,
    sorted by its vertical (y) center.
    """
    ocr_model = get_ocr_model()
    raw = ocr_model.ocr(img_rgb, cls=True)[0]

    print(f"🔍 simple_cells: OCR detected {len(raw) if raw else 0} items")
    
    if not raw:
        print("❌ OCR returned no results at all")
        return []

    cells = []
    for i, (box, (text, confidence)) in enumerate(raw):
        print(f"  Item {i}: '{text}' (conf: {confidence:.2f})")
        raw_text = text.strip()  #new line added
        if not raw_text:  #new line added
            print(f"    ⚠️ Skipped: empty text")
            continue      #new line added
        cleaned = fix_diameter(raw_text)   #new line added       
        # if not text.strip():
        #     continue
        # compute the vertical midpoint for sorting
        y_center = int((box[0][1] + box[2][1]) / 2)
        cells.append({
            "y_center": y_center,
            "text": cleaned,    #text.strip()
            "confidence": confidence
        })

    print(f"📊 simple_cells: Returning {len(cells)} valid cells")
    # stable sort top→bottom
    cells.sort(key=lambda c: c["y_center"])
    # drop the y_center before returning
    return [{"text": c["text"], "confidence": c["confidence"]} for c in cells]


def advanced_cells_with_rectangles(img):
    # 1) resize+decode as before...
    #    (make sure `img` here is your OpenCV BGR image)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, bw = cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY_INV)

    h, w = img.shape[:2]

    # 2) horizontal strokes
    horiz_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(5, w//80), 1))
    horiz = cv2.morphologyEx(bw, cv2.MORPH_OPEN, horiz_kernel)

    # 3) vertical strokes
    vert_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(5, h//80)))
    vert = cv2.morphologyEx(bw, cv2.MORPH_OPEN, vert_kernel)

    # —— now CLOSE each so broken segments reconnect —— 
    horiz = cv2.morphologyEx(horiz,
                             cv2.MORPH_CLOSE,
                             np.ones((3,3), np.uint8),
                             iterations=1)
    vert  = cv2.morphologyEx(vert,
                             cv2.MORPH_CLOSE,
                             np.ones((3,3), np.uint8),
                             iterations=1)

    # 4) AND them to get only true grid‐lines
    grid = cv2.bitwise_and(horiz, vert)

    # —— final closing so tiny gaps don’t break a cell in two ——
    grid = cv2.morphologyEx(grid,
                             cv2.MORPH_CLOSE,
                             np.ones((5,5), np.uint8),
                             iterations=1)
    

    # # 5) optional: dilate so borders join cleanly into rectangles
    # grid = cv2.dilate(grid, np.ones((3,3), np.uint8), iterations=1)

    # 6) find all contours on that grid
    contours, _ = cv2.findContours(grid, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    rects = []
    for cnt in contours:
        x, y, rw, rh = cv2.boundingRect(cnt)
        # throw away anything too small to be a cell
        if rw < w//20 or rh < h//30:
            continue
        rects.append((x, y, rw, rh))

    # === DEBUG VISUALIZATION START ===
    # draw all the rects you’ve kept onto a copy of the image
    debug_img = img.copy()
    for (x, y, rw, rh) in rects:
        cv2.rectangle(debug_img, (x, y), (x+rw, y+rh), (0,255,0), 2)
    # save to a temp file so you can inspect it
    debug_path = f"/tmp/cell_debug_{uuid.uuid4().hex[:8]}.png"
    cv2.imwrite(debug_path, debug_img)
    print("🛠️  Debug cell map written to:", debug_path)
    # === DEBUG VISUALIZATION END ===    

    
    
    # if we found **no** real rectangles, fall back
    if not rects:
        print("⚠️  No rectangles detected, falling back to simple_cells")
        return simple_cells(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))

    # 7) sort the rectangles top→bottom, left→right
    rects.sort(key=lambda r: (r[1], r[0]))

    # 8) do one OCR pass and map each snippet into its containing rect
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    raw = get_ocr_model().ocr(rgb, cls=True)[0]
    cells = []
    for box, (text, conf) in raw:
        raw_text = text.strip()
        # if not text.strip(): 
        #     continue
        if not raw_text:
            continue
        cleaned = fix_diameter(raw_text)
        mx = int((box[0][0] + box[2][0]) / 2)
        my = int((box[0][1] + box[2][1]) / 2)
        # find which rectangle contains this midpoint
        for idx, (x, y, rw, rh) in enumerate(rects):
            if x <= mx < x+rw and y <= my < y+rh:
                cells.append((idx, mx, my, cleaned, conf))
                break

    # 9) for each rect‐index, collect its bits, sort by x (then y), glue text
    out = []
    for i, (x, y, rw, rh) in enumerate(rects):
        bucket = [(mx, my, t, c) for (idx, mx, my, t, c) in cells if idx == i]
        if not bucket:
            out.append({"text": "", "confidence": 0})
            continue
        # reading order inside a cell: left→right, top→bottom
        bucket.sort(key=lambda e: (e[1], e[0]))
        joined = " ".join(e[2] for e in bucket)
        conf   = min(e[3] for e in bucket)
        out.append({"text": joined, "confidence": conf})

    return out


def advanced_cells(img):
    # 1) Single OCR pass (RGB)
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    raw = get_ocr_model().ocr(rgb, cls=True)[0]

    # 2) Estimate a “typical” line‐height and set merge_thresh = max(median_h, 20px)
    heights = [abs(box[2][1] - box[0][1]) for box, (txt, _) in raw if txt.strip()]
    if heights:
        median_h = sorted(heights)[len(heights)//2]
        merge_thresh = max(median_h, 20)
    else:
        merge_thresh = 20

    # 3) Binarize & invert for horizontal‐line detection
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, bw = cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY_INV)

    # 4) Extract horizontal strokes
    h, w = img.shape[:2]
    kern = cv2.getStructuringElement(cv2.MORPH_RECT, (max(5, w//80), 1))
    horiz = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kern)

    # 5) Hough‐detect those strokes (even short ones)
    lines = cv2.HoughLinesP(
        horiz,
        rho=1, theta=np.pi/180,
        threshold=30,
        minLineLength=w//40,
        maxLineGap=5
    )

    # 6) Cluster all y‐coordinates of detected lines into row_bounds
    ys = []
    if lines is not None:
        for x1, y1, x2, y2 in lines[:,0]:
            ys += [y1, y2]
    ys.sort()

    clusters = []
    for y in ys:
        if not clusters or abs(y - clusters[-1][0]) > merge_thresh:
            clusters.append([y])
        else:
            clusters[-1].append(y)
    row_bounds = [int(sum(c)/len(c)) for c in clusters]

    # 7) Fallback: if we found no interior lines, drop back to your old simple_cells
    if lines is None or len(row_bounds) < 2:
        return simple_cells(rgb)

    # 8) Bucket the same OCR boxes into those horizontal bands
    cells = []
    for box, (txt, conf) in raw:
        if not txt.strip():
            continue
        x = int((box[0][0] + box[2][0]) / 2)
        y = int((box[0][1] + box[2][1]) / 2)
        cells.append({"x": x, "y": y, "text": txt.strip(), "conf": conf})

    rows = []
    # head (above the first grid line)
    head = [c for c in cells if c["y"] < row_bounds[0]]
    if head:
        head.sort(key=lambda c: (c["y"], c["x"]))
        rows.append({
            "text": " ".join(c["text"] for c in head),
            "confidence": min(c["conf"] for c in head)
        })

    # middle bands
    for top, bot in zip(row_bounds, row_bounds[1:]):
        band = [c for c in cells if top <= c["y"] < bot]
        if not band:
            continue
        band.sort(key=lambda c: (c["y"], c["x"]))
        rows.append({
            "text": " ".join(c["text"] for c in band),
            "confidence": min(c["conf"] for c in band)
        })

    # tail (below the last grid line)
    tail = [c for c in cells if c["y"] >= row_bounds[-1]]
    if tail:
        tail.sort(key=lambda c: (c["y"], c["x"]))
        rows.append({
            "text": " ".join(c["text"] for c in tail),
            "confidence": min(c["conf"] for c in tail)
        })

    return rows

# Working fine except multiline text extraction
# def advanced_cells(img):

#     # 1) Run one OCR pass to get raw boxes
#     rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
#     raw = get_ocr_model().ocr(rgb, cls=True)[0]

#     # 2) Estimate a typical line-height from the OCR boxes
#     heights = [abs(box[2][1] - box[0][1]) for box,(_,_) in raw]
#     if heights:
#         median_h = sorted(heights)[len(heights)//2]
#         # half a line-height but never below 5px
#         merge_thresh = max(5, median_h // 2)
#     else:
#         # fallback if OCR saw nothing
#         merge_thresh = 5

#     # 3) Binarize & invert for line-detect
#     gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
#     _, bw = cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY_INV)

#     # 4) Extract horizontal strokes
#     h, w = img.shape[:2]
#     kern = cv2.getStructuringElement(cv2.MORPH_RECT, (max(5, w//80), 1))
#     horiz = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kern)

#     # 5) Hough for even short lines
#     lines = cv2.HoughLinesP(
#         horiz, rho=1, theta=np.pi/180,
#         threshold=30, minLineLength=w//40, maxLineGap=5
#     )

#     # 6) Cluster all y’s into row_bounds using the dynamic threshold
#     ys = []
#     if lines is not None:
#         for x1,y1,x2,y2 in lines[:,0]:
#             ys += [y1, y2]
#     ys.sort()

#     clusters = []
#     for y in ys:
#         if not clusters or abs(y - clusters[-1][0]) > merge_thresh:
#             clusters.append([y])
#         else:
#             clusters[-1].append(y)
#     row_bounds = [int(sum(c)/len(c)) for c in clusters]

#     # 7) FALLBACK if no lines or only one cluster
#     if lines is None or len(row_bounds) < 2:
#         return simple_cells(rgb)

#     # 8) Now bucket the same raw OCR into those bands
#     cells = []
#     for box,(t,c) in raw:
#         if not t.strip(): continue
#         x = int((box[0][0]+box[2][0]) / 2)
#         y = int((box[0][1]+box[2][1]) / 2)
#         cells.append({"x": x, "y": y, "text": t.strip(), "conf": c})

#     rows = []
#     # head (above first line)
#     head = [c for c in cells if c["y"] < row_bounds[0]]
#     if head:
#         head.sort(key=lambda c:(c["y"],c["x"]))
#         rows.append({
#             "text": " ".join(c["text"] for c in head),
#             "confidence": min(c["conf"] for c in head)
#         })

#     # middle bands
#     for top, bot in zip(row_bounds, row_bounds[1:]):
#         band = [c for c in cells if top <= c["y"] < bot]
#         if not band: continue
#         band.sort(key=lambda c:(c["y"],c["x"]))
#         rows.append({
#             "text": " ".join(c["text"] for c in band),
#             "confidence": min(c["conf"] for c in band)
#         })

#     # tail (below last line)
#     tail = [c for c in cells if c["y"] >= row_bounds[-1]]
#     if tail:
#         tail.sort(key=lambda c:(c["y"],c["x"]))
#         rows.append({
#             "text": " ".join(c["text"] for c in tail),
#             "confidence": min(c["conf"] for c in tail)
#         })

#     return rows

# def process_image(image_bytes):
#     import cv2, numpy as np

#     # —————————————————————————————
#     # 1) Decode & resize exactly as before
#     nparr = np.frombuffer(image_bytes, np.uint8)
#     img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
#     max_dim = 800
#     h, w = img.shape[:2]
#     if max(h, w) > max_dim:
#         scale = max_dim / max(h, w)
#         img = cv2.resize(img, (int(w*scale), int(h*scale)))
    
#     # 2) Prepare a gray/binary image for line detection
#     gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
#     _, bw = cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY_INV)
    
#     # 3) Extract long horizontal strokes (the table grid‐lines)
#     horiz_kernel = cv2.getStructuringElement(
#         cv2.MORPH_RECT, (max(20, img.shape[1]//30), 1)
#     )
#     horiz = cv2.morphologyEx(bw, cv2.MORPH_OPEN, horiz_kernel)
    
#     # 4) Hough‐detect those strokes
#     lines = cv2.HoughLinesP(
#         horiz, rho=1, theta=np.pi/180, threshold=100,
#         minLineLength=img.shape[1]//2, maxLineGap=20
#     )
#     ys = []
#     if lines is not None:
#         for x1, y1, x2, y2 in lines[:,0]:
#             ys += [y1, y2]
#     ys.sort()
    
#     # 5) Cluster y’s that are within 5px of each other → true row boundaries
#     clusters = []
#     for y in ys:
#         if not clusters or abs(y - clusters[-1][0]) > 5:
#             clusters.append([y])
#         else:
#             clusters[-1].append(y)
#     row_bounds = sorted(int(sum(c)/len(c)) for c in clusters)
    
#     # —————————————————————————————
#     # 6) Finally run your OCR on the (RGB) image
#     image_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
#     results = get_ocr_model().ocr(image_rgb, cls=True)[0]
    
#     # 7) Compute each snippet’s center
#     cells = []
#     for box, (text, conf) in results:
#         if not text.strip(): 
#             continue
#         x_c = int((box[0][0] + box[2][0]) / 2)
#         y_c = int((box[0][1] + box[2][1]) / 2)
#         cells.append({"x": x_c, "y": y_c, "text": text.strip(), "conf": conf})
    
#     # 8) Group into bands between each consecutive pair of row_bounds
#     rows = []
#     for i in range(len(row_bounds) - 1):
#         top, bot = row_bounds[i], row_bounds[i+1]
#         band = [c for c in cells if top <= c["y"] < bot]
#         if not band:
#             continue
#         band.sort(key=lambda c: c["x"])
#         merged_text = " ".join(c["text"] for c in band)
#         merged_conf = min(c["conf"] for c in band)
#         rows.append({"text": merged_text, "confidence": merged_conf})
    
#     return rows

    
# def process_image(image_bytes):
#     logger.info(f"Processing image of size {len(image_bytes)} bytes")
    
#     # Convert to OpenCV format
#     nparr = np.frombuffer(image_bytes, np.uint8)
#     img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    
#     # Resize image for faster processing
#     max_dim = 800
#     height, width = img.shape[:2]
#     if max(height, width) > max_dim:
#         scale = max_dim / max(height, width)
#         img = cv2.resize(img, (int(width * scale), int(height * scale)))
#         logger.info(f"Resized image to {img.shape[1]}x{img.shape[0]}")
    
#     # Convert to RGB for OCR
#     image_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    
#     # Process with OCR
#     logger.info("Starting OCR processing")
#     ocr_model = get_ocr_model()
#     results = ocr_model.ocr(image_rgb, cls=True)
#     cells = []
    
#     if results and len(results) > 0 and len(results[0]) > 0:
#         for box, (text, confidence) in results[0]:
#             if text.strip():
#                 y_center = int((box[0][1] + box[2][1]) / 2)
#                 cells.append({"y_center": y_center, "text": text.strip(), "confidence": confidence})
#         cells.sort(key=lambda c: c["y_center"])
    
#     logger.info(f"OCR completed, found {len(cells)} text elements")
#     return cells

# Health check endpoint
@app.get("/health")
async def health_check():
    model_paths_exist = all([
        os.path.exists(DET_MODEL_DIR),
        os.path.exists(REC_MODEL_DIR),
        os.path.exists(CLS_MODEL_DIR)
    ])
    return {
        "status": "ok", 
        "model_files_exist": model_paths_exist,
        "paddle_home": PADDLE_HOME
    }

# Test endpoint
@app.get("/test")
async def test_endpoint():
    return {"status": "ok", "message": "API is working"}

# Handle HEAD requests
@app.head("/")
async def head_endpoint():
    return JSONResponse(content={"status": "ok"})

# Main OCR endpoint
@app.post("/")
async def ocr_endpoint(request: Request):
    try:
        logger.info(f"Received OCR request with Content-Type: {request.headers.get('content-type')}")

        # 1) Get form + fields
        form = await request.form()
        logger.info(f"Received form data with keys: {list(form.keys())}")

        if "image" not in form:
            logger.warning("Missing image parameter in request")
            return JSONResponse(status_code=400, content={"error": "Missing image parameter"})
        if "mode" not in form:
            logger.warning("Missing mode parameter in request")
            return JSONResponse(status_code=400, content={"error": "Missing mode parameter"})
        # ← NEW: optional “column” tag
        column_id = form.get("column", None)

        image_file = form["image"]
        mode       = form["mode"]
        logger.info(f"Processing request in mode: {mode}, image filename: {image_file.filename}, column: {column_id}")

        # 2) Read the bytes
        image_bytes = await image_file.read()

        # 3) Decode once to a CV2 image
        nparr = np.frombuffer(image_bytes, np.uint8)
        img   = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        # 4) Dispatch to the right OCR routine under a worker thread
        def do_ocr():
            # quick mode: just raw text join
            if mode == "quick":
                rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                # reuse simple_cells to get list of dicts
                cells = simple_cells(rgb)
                extracted_text = "\n".join(c["text"] for c in cells)
                return {"mode": mode, "extracted_text": extracted_text, "cells": cells}

            # table mode: choose by column tag
            elif mode == "table":
                rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                table_cells = simple_cells(rgb)
                logger.info(f"Using simple_cells for column: {column_id}")
                return {"mode": mode, "table": table_cells}
                # # quantity gets the old per‐line logic
                # if column_id == "quantity":
                #     rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                #     table_cells = simple_cells(rgb)
                # # everything else uses the fancy line‐based
                # else:
                #     table_cells = advanced_cells_with_rectangles(img)
                # return {"mode": mode, "table": table_cells}

            else:
                raise ValueError(f"Invalid mode provided: {mode}")

        # 5) Run OCR with timeout protection
        try:
            result = await asyncio.to_thread(do_ocr)
            return result

        except asyncio.TimeoutError:
            logger.error("OCR processing timed out")
            return JSONResponse(
                status_code=504,
                content={"error": "Processing timed out. Try with a smaller image."}
            )

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logger.error(f"Error processing request: {e}\n{tb}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Server error: {str(e)}"}
        )

# Extracts Drawing number from File Name
def extract_drawing_number(url: str):
    if not url:
        return ""
    match = re.search(r"/([^/]+)\.pdf$", url, re.IGNORECASE)
    return match.group(1) if match else ""

# Updates the lastItem Number in glide Drawing table
async def update_last_ocr_bom_item_direct(row_id: str, new_last_item: int):
    """Update lastOcrBomItem using direct rowID"""
    try:
        print(f"🔄 Updating lastOcrBomItem to {new_last_item} for rowID: {row_id}")
        
        update_body = {
            "appID": GLIDE_APP_ID,
            "mutations": [{
                "kind": "set-columns-in-row",
                "tableName": GLIDE_TABLE,
                "columnValues": {"WddPP": new_last_item},
                "rowID": row_id
            }]
        }
        
        async with httpx.AsyncClient() as client:
            update_res = await client.post(
                "https://api.glideapp.io/api/function/mutateTables",
                headers={"Authorization": f"Bearer {GLIDE_API_KEY}", "Content-Type": "application/json"},
                json=update_body
            )
        
        update_res.raise_for_status()
        result = update_res.json()
        print(f"✅ Successfully updated lastOcrBomItem to {new_last_item}")
        return True
        
    except Exception as e:
        print(f"❌ Error updating lastOcrBomItem: {e}")
        return False

# API code to fetch drawing numbers (File name)
GLIDE_API_KEY = os.getenv("GLIDE_API_KEY")
GLIDE_APP_ID = os.getenv("GLIDE_APP_ID")
GLIDE_TABLE = os.getenv("GLIDE_TABLE")
# ZAPIER_WEBHOOK_URL = "YOUR_ACTUAL_ZAPIER_WEBHOOK_URL_HERE"

#Cloudinary configuration
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET")
)


@app.post("/fetch-drawings")
async def fetch_drawings(request: Request):
    print("🎯 /fetch-drawings endpoint hit")

    try:
        payload = await request.json()
        project = payload.get("project")
        part_number = payload.get("part")

        print("📦 Incoming fetch-drawings request:", project, part_number)
        
        if not project or not part_number:
            return {"error": "Missing project or part"}

        # ✅ Correct API format
        body = {
            "appID": GLIDE_APP_ID,
            "queries": [
                {
                    "tableName": GLIDE_TABLE,
                    "utc": True
                }
            ]
        }
        
        print("📤 Sending request to Glide:", body)

        async with httpx.AsyncClient() as client:
            res = await client.post(
                "https://api.glideapp.io/api/function/queryTables",
                headers={
                    "Authorization": f"Bearer {GLIDE_API_KEY}",
                    "Content-Type": "application/json"
                },
                json=body
            )

        res.raise_for_status()
        
        try:
            full_data = res.json()
            print("✅ Full response from Glide:", full_data)
            # ✅ Extract and filter rows manually
            all_rows = full_data[0]["rows"]
            filtered = [
                row for row in all_rows
                if row.get("VQlMl") == project and row.get("nlHAO") == part_number
            ]

            print(f"✅ Filtered rows: {len(filtered)} match")
            # ✅ Add extracted drawingNumber from drawing link
            filtered_trimmed = [
                {
                    "project": row.get("VQlMl"),
                    "partNumber": row.get("nlHAO"),
                    "partName": row.get("Name"),
                    "drawingLink": row.get("9iB5E"),
                    "drawingNumber": extract_drawing_number(row.get("9iB5E"))
                }
                for row in filtered
            ]
        
            return {"rows": filtered_trimmed}
        
        except Exception:
            error_text = await res.aread()
            print("❌ Non-JSON response from Glide:", error_text.decode())
            raise


    except Exception as e:
        import traceback
        print("❌ Exception occurred:")
        print(traceback.format_exc())
        return JSONResponse(status_code=500, content={"error": str(e)})


# Add these endpoints to your existing FastAPI backend (main.py)
@app.post("/add-child-parts")
async def add_child_parts(request: Request):
    """Add child parts data to Glide Child Parts table"""
    print("🎯 /add-child-parts endpoint hit")
    
    try:
        payload = await request.json()
        rows_data = payload.get("rows", [])
        project = payload.get("project")
        parent_drawing_number = payload.get("parentDrawingNumber")
        part_number = payload.get("partNumber")  # Overall Part Number from URL
        rowID = payload.get("rowID")
        maxItemNumber = payload.get("maxItemNumber")
        
        print(f"📦 Child Parts Request: project={project}, parent={parent_drawing_number}, part={part_number}")
        print(f"📊 Rows to add: {len(rows_data)}, rowID: {rowID}, maxItemNumber: {maxItemNumber}")
        
        if not project or not parent_drawing_number or not part_number:
            return JSONResponse(
                status_code=400, 
                content={"error": "Missing required parameters: project, parentDrawingNumber, or partNumber"}
            )
        
        if not rows_data:
            return JSONResponse(
                status_code=400,
                content={"error": "No rows data provided"}
            )
        
        # Build mutations for Child Parts table
        mutations = []
        for row in rows_data:         
            mutation = {
                "kind": "add-row-to-table",
                "tableName": "native-table-3HZdeQgfDL37ac2rc3kF",  # Child Parts table
                "columnValues": {
                    "remote\u001dPart number": part_number,  # Overall Part Number from URL
                    "remote\u001dParent drawing number": parent_drawing_number,
                    "remote\u001dDrawing number": row.get("drawingNumber", ""),  # Select Drawing Number
                    "remote\u001dQuantity": str(row.get("quantity", "")),
                    "remote\u001dProject Name": project,
                    "qkM5k": row.get("description", ""),
                    "JIGhW": row.get("material", ""),
                    "remote\u001dItem #": row.get("itemNumber"),
                    "Inzp9":row.get("ocrWarning", "")
                    # Note: Item # is not being sent as per your requirement
                }
            }
            mutations.append(mutation)
        
        if not mutations:
            return JSONResponse(
                status_code=400,
                content={"error": "No valid rows to add (missing required fields)"}
            )
        
        # Prepare Glide API request
        glide_body = {
            "appID": GLIDE_APP_ID,
            "mutations": mutations
        }
        
        print(f"📤 Sending {len(mutations)} child parts to Glide...")
        
        # Send to Glide API
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.glideapp.io/api/function/mutateTables",
                headers={
                    "Authorization": f"Bearer {GLIDE_API_KEY}",
                    "Content-Type": "application/json"
                },
                json=glide_body
            )
        
        response.raise_for_status()
        result = response.json()
        
        print("✅ Child Parts added successfully:", result)

        if rowID and maxItemNumber:
            update_success = await update_last_ocr_bom_item_direct(rowID, maxItemNumber)
            print(f"🎯 Drawing table update result: {update_success}") 
        return {
            "success": True, 
            "message": f"Successfully added {len(mutations)} child parts",
            "glide_response": result
        }
        
    except httpx.HTTPStatusError as e:
        error_text = await e.response.aread()
        print(f"❌ Glide API Error: {e.response.status_code} - {error_text.decode()}")
        return JSONResponse(
            status_code=e.response.status_code,
            content={"error": f"Glide API error: {error_text.decode()}"}
        )
    except Exception as e:
        import traceback
        print("❌ Exception in add_child_parts:")
        print(traceback.format_exc())
        return JSONResponse(
            status_code=500,
            content={"error": f"Server error: {str(e)}"}
        )


@app.post("/add-bo-parts")
async def add_bo_parts(request: Request):
    """Add BO (Bought Out) parts data to Glide BO Parts table"""
    print("🎯 /add-bo-parts endpoint hit")
    
    try:
        payload = await request.json()
        rows_data = payload.get("rows", [])
        project = payload.get("project")
        parent_drawing_number = payload.get("parentDrawingNumber")
        part_number = payload.get("partNumber")  # Overall Part Number from URL

        rowID = payload.get("rowID")
        maxItemNumber = payload.get("maxItemNumber")
        
        print(f"📦 BO Parts Request: project={project}, parent={parent_drawing_number}, part={part_number}")
        print(f"📊 Rows to add: {len(rows_data)}")
        
        if not project or not parent_drawing_number or not part_number:
            return JSONResponse(
                status_code=400,
                content={"error": "Missing required parameters: project, parentDrawingNumber, or partNumber"}
            )
        
        if not rows_data:
            return JSONResponse(
                status_code=400,
                content={"error": "No rows data provided"}
            )
        
        # Build mutations for BO Parts table
        mutations = []
        for row in rows_data:       
            mutation = {
                "kind": "add-row-to-table",
                "tableName": "native-table-l2JX33tUJwUKYmNz7ZEs",  # BO Parts table
                "columnValues": {
                    "remote\u001dProject name": project,
                    "remote\u001dOverall Part number": part_number,  # Overall Part Number from URL
                    "remote\u001dParent drawing": parent_drawing_number,
                    "remote\u001dBoughout Part number": row.get("boughtoutPartNumber", ""),  # Drawing Number or Frontend Part Number
                    "remote\u001dDescription": row.get("description", ""),
                    "remote\u001dMOC": row.get("material", ""),  # Material goes to MOC field
                    "remote\u001dQuantity": str(row.get("quantity", "")),
                    "JPBNt": row.get("ocrWarning", ""),
                    "wRubP": row.get("itemNumber")
                    # Note: cbN8e (Last updated at), remote\u001dItem number, and 8Kjom (Boughtout rate) are not being sent
                }
            }
            mutations.append(mutation)
        
        if not mutations:
            return JSONResponse(
                status_code=400,
                content={"error": "No valid rows to add (missing required fields)"}
            )
        
        # Prepare Glide API request
        glide_body = {
            "appID": GLIDE_APP_ID,
            "mutations": mutations
        }
        
        print(f"📤 Sending {len(mutations)} BO parts to Glide...")
        
        # Send to Glide API
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.glideapp.io/api/function/mutateTables",
                headers={
                    "Authorization": f"Bearer {GLIDE_API_KEY}",
                    "Content-Type": "application/json"
                },
                json=glide_body
            )
        
        response.raise_for_status()
        result = response.json()
        
        print("✅ BO Parts added successfully:", result)

        if rowID and maxItemNumber:
            update_success = await update_last_ocr_bom_item_direct(rowID, maxItemNumber)
            print(f"🎯 Drawing table update result: {update_success}") 

        return {
            "success": True,
            "message": f"Successfully added {len(mutations)} BO parts", 
            "glide_response": result
        }
        
    except httpx.HTTPStatusError as e:
        error_text = await e.response.aread()
        print(f"❌ Glide API Error: {e.response.status_code} - {error_text.decode()}")
        return JSONResponse(
            status_code=e.response.status_code,
            content={"error": f"Glide API error: {error_text.decode()}"}
        )
    except Exception as e:
        import traceback
        print("❌ Exception in add_bo_parts:")
        print(traceback.format_exc())
        return JSONResponse(
            status_code=500,
            content={"error": f"Server error: {str(e)}"}
        )
# Childpart & BO Data Post Ends here

# @app.post("/generate-missing-childpart-pdf")
# async def generate_missing_pdf(request: Request):
#     # Get project and part number from query params
#     url_params = dict(request.query_params)
#     project = url_params.get("project")
#     part_number = url_params.get("part")

#     # Get matchedPart and zapierWebhookUrl from payload
#     payload = await request.json()
#     matched_part = payload.get("matchedPart") or part_number

#     if not project or not part_number:
#         return JSONResponse(
#             status_code=400,
#             content={"error": "Missing required parameters: project, part (in query), or zapierWebhookUrl (in body)"}
#         )

#     # 1. Create PDF file
#     pdf = FPDF(orientation='L', unit='mm', format='A4')
#     pdf.add_page()
#     pdf.set_font("Arial", size=24)
#     pdf.cell(0, 60, txt="Missing Child Part Drawing", ln=True, align='C')
#     pdf.set_font("Arial", size=18)
#     pdf.cell(0, 10, txt=f"Part Number: {matched_part}", ln=True, align='C')

#     # Use a temp file for PDF
#     with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
#         filename = tmp.name
#         pdf.output(filename)

#     # 2. Send to Zapier
#     with open(filename, 'rb') as f:
#         files = {'file': (f"Missing_ChildPart_{matched_part}.pdf", f, 'application/pdf')}
#         data = {
#             'project': project,
#             'partNumber': part_number
#         }
#         response = requests.post(ZAPIER_WEBHOOK_URL, data=data, files=files)

#     os.remove(filename)

#     return {"status": "PDF sent", "zapier_status_code": response.status_code}


# --- Generate Missing Child Part PDF and upload directly to Glide Drawing Table ---

# @app.post("/generate-missing-childpart-pdf")
# async def generate_missing_childpart_pdf(request: Request):
#     """Generate Missing Child Part PDF and upload directly to Glide Drawing Table"""
#     try:
#         # Get project and part number from query params (like your old code)
#         url_params = dict(request.query_params)
#         project = url_params.get("project")
#         part_number = url_params.get("part")

#         payload = await request.json()
#         matched_part = payload.get("matchedPart")

#         if not matched_part or not project or not part_number:
#             return JSONResponse(
#                 status_code=400,
#                 content={"error": "Missing required fields: matchedPart, project, partNumber"}
#             )

#         # 1️⃣ Generate PDF in-memory
#         pdf = FPDF(orientation="L", unit="mm", format="A4")
#         pdf.add_page()
#         pdf.set_font("Arial", size=24)
#         pdf.cell(0, 20, txt="Missing Child Part Drawing", ln=True, align="C")
#         pdf.set_font("Arial", size=18)
#         pdf.cell(0, 15, txt=f"Part Number: {matched_part}", ln=True, align="C")

#         pdf_file_path = f"/tmp/{matched_part}.pdf"
#         pdf.output(pdf_file_path)

#         # 2️⃣ Prepare Glide mutation (Drawing table)
#         mutations = [
#             {
#                 "kind": "add-row-to-table",
#                 "tableName": GLIDE_TABLE,  # same table as fetch-drawings
#                 "columnValues": {
#                     "VQlMl": project,        # Project column
#                     "nlHAO": part_number,     # Part number column
#                     "9iB5E": "file"
#                 }
#             }
#         ]

#         url = "https://api.glideapp.io/api/function/mutateTables"
#         headers = {"Authorization": f"Bearer {GLIDE_API_KEY}"}

#         # 3️⃣ Send multipart request to Glide
#         with open(pdf_file_path, "rb") as file:
#             form_data = {
#                 "appID": (None, GLIDE_APP_ID),
#                 "mutations": (None, json.dumps(mutations)),
#                 "file": (f"{matched_part}.pdf", file, "application/pdf")
#             }

#             async with httpx.AsyncClient() as client:
#                 response = await client.post(url, headers=headers, files=form_data)
#                 response.raise_for_status()
#                 result = response.json()

#         os.remove(pdf_file_path)
        
#         return {
#             "success": True,
#             "message": "PDF uploaded directly to Glide Drawing Table",
#             "glide_response": result
#         }

#     except Exception as e:
#         import traceback
#         print("❌ Error in generate_missing_childpart_pdf:")
#         print(traceback.format_exc())
#         return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/generate-missing-childpart-pdf")
async def generate_missing_childpart_pdf(request: Request):
    """Generate Missing Child Part PDF, upload to Cloudinary, and save URL in Glide Drawing Table"""
    try:
        # Get project and part number from query params (like your old code)
        url_params = dict(request.query_params)
        project = url_params.get("project")
        part_number = url_params.get("part")

        payload = await request.json()
        matched_part = payload.get("matchedPart")
        matched_part = matched_part.replace(".", "") if matched_part else None

        if not matched_part or not project or not part_number:
            return JSONResponse(
                status_code=400,
                content={"error": "Missing required fields: matchedPart, project, partNumber"}
            )

        # 1️⃣ Generate PDF in-memory
        # pdf = FPDF(orientation="L", unit="mm", format="A4")
        # pdf.add_page()
        # pdf.set_font("Arial", size=24)
        # pdf.cell(0, 20, txt="Missing Child Part Drawing", ln=True, align="C")
        # pdf.set_font("Arial", size=18)
        # pdf.cell(0, 15, txt=f"Part Number: {matched_part}", ln=True, align="C")

        # pdf_file_path = f"/tmp/{matched_part}.pdf"
        # pdf.output(pdf_file_path)

        # 1️⃣ Generate PDF in-memory
        pdf = FPDF(orientation="L", unit="mm", format="A4")
        pdf.add_page()
        
        # Move down from top to center the content vertically
        pdf.ln(60)  # Move down 80mm from top (roughly center for landscape A4)
        
        # Title - larger font
        pdf.set_font("Arial", size=32)  # Increased from 24
        pdf.cell(0, 15, txt="Missing Drawing", ln=True, align="C")
        
        # Add some space between title and part number
        pdf.ln(5)  # 10mm gap
        
        # Part number - larger font  
        pdf.set_font("Arial", size=24)  # Increased from 18
        pdf.cell(0, 12, txt=f"Part Number: {matched_part}", ln=True, align="C")
        
        pdf_file_path = f"/tmp/{matched_part}.pdf"
        pdf.output(pdf_file_path)


        # 2️⃣ Upload PDF to Cloudinary
        try:
            with open(pdf_file_path, "rb") as file:
                upload_result = cloudinary.uploader.upload(
                    file,
                    resource_type="raw",               # Required for PDF
                    public_id=f"missing-pdfs/{matched_part}",  # File name will be included
                    overwrite=True
                )
                pdf_url = upload_result["secure_url"]
                print(f"✅ PDF uploaded to Cloudinary: {pdf_url}")
                
        except Exception as cloudinary_error:
            print(f"❌ Cloudinary upload failed: {cloudinary_error}")
            # Clean up temp file on Cloudinary error
            if os.path.exists(pdf_file_path):
                os.remove(pdf_file_path)
            raise Exception(f"Failed to upload PDF to Cloudinary: {str(cloudinary_error)}")

        # Clean up temp file after successful upload
        if os.path.exists(pdf_file_path):
            os.remove(pdf_file_path)

        # 3️⃣ Prepare Glide mutation (Drawing table) with Cloudinary URL
        mutations = [
            {
                "kind": "add-row-to-table",
                "tableName": GLIDE_TABLE,  # same table as fetch-drawings
                "columnValues": {
                    "VQlMl": project,         # Project column
                    "nlHAO": part_number,     # Part number column
                    "9iB5E": pdf_url          # Assuming 9iB5E is the drawing link column
                }
            }
        ]

        url = "https://api.glideapp.io/api/function/mutateTables"
        headers = {
            "Authorization": f"Bearer {GLIDE_API_KEY}",
            "Content-Type": "application/json"
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                headers=headers,
                json={"appID": GLIDE_APP_ID, "mutations": mutations}
            )
            response.raise_for_status()
            result = response.json()

        return {
            "success": True,
            "message": "PDF uploaded to Cloudinary and URL saved in Glide Drawing Table",
            "pdf_url": pdf_url,
            "glide_response": result
        }

    except Exception as e:
        import traceback
        print("❌ Error in generate_missing_childpart_pdf:")
        print(traceback.format_exc())
        return JSONResponse(status_code=500, content={"error": str(e)})



@app.get("/debug")
async def debug():
    print("✅ /debug route hit", flush=True)
    return {"ok": True}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)

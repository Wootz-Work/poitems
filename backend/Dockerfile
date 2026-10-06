FROM python:3.10-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxrender1 \
    libxext6 \
    libgomp1 \
    ca-certificates \
    wget \
 && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download PaddleOCR model files
RUN mkdir -p ~/.paddleocr/whl/det/en/ \
    && mkdir -p ~/.paddleocr/whl/rec/en/ \
    && mkdir -p ~/.paddleocr/whl/cls/ \
    && wget https://paddleocr.bj.bcebos.com/PP-OCRv3/english/en_PP-OCRv3_det_infer.tar -P ~/.paddleocr/whl/det/en/ \
    && wget https://paddleocr.bj.bcebos.com/PP-OCRv3/english/en_PP-OCRv3_rec_infer.tar -P ~/.paddleocr/whl/rec/en/ \
    && wget https://paddleocr.bj.bcebos.com/dygraph_v2.0/ch/ch_ppocr_mobile_v2.0_cls_infer.tar -P ~/.paddleocr/whl/cls/ \
    && cd ~/.paddleocr/whl/det/en/ && tar -xf en_PP-OCRv3_det_infer.tar \
    && cd ~/.paddleocr/whl/rec/en/ && tar -xf en_PP-OCRv3_rec_infer.tar \
    && cd ~/.paddleocr/whl/cls/ && tar -xf ch_ppocr_mobile_v2.0_cls_infer.tar \
    && chmod -R 755 ~/.paddleocr

# Verify model files were downloaded correctly
RUN ls -la ~/.paddleocr/whl/det/en/en_PP-OCRv3_det_infer/ \
    && ls -la ~/.paddleocr/whl/rec/en/en_PP-OCRv3_rec_infer/ \
    && ls -la ~/.paddleocr/whl/cls/ch_ppocr_mobile_v2.0_cls_infer/

# Copy application code
COPY main.py po_assemblies.py ./

# Set environment variables
ENV PORT=8000

# Expose the port
EXPOSE 8000

# Command to run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

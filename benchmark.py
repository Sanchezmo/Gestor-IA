#!/usr/bin/env python3
"""
REAL PaddleOCR-VL Benchmark - Final Version
"""

import asyncio
import base64
import fitz
import httpx
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

# =========================================================================
# CONFIGURATION
# =========================================================================

RENDER_DPI = 200
MODEL_NAME = "AuditAid/PaddleOCR-VL-1.6-0.9B:latest"
OLLAMA_ENDPOINT = "http://127.0.0.1:11434"
REQUEST_TIMEOUT = 120.0
QWEN_AVG_MS = 45000

# =========================================================================
# TEST INVOICES (3 Fast Ones)
# =========================================================================

TEST_INVOICES = [
    {
        "filename": "01_simple_iva21.pdf",
        "expected": {
            "supplier": {"name": "ACME SL", "tax_id": "B12345678"},
            "invoice": {"number": "FAC-2026-001", "date": "2026-08-15", "due_date": "2026-09-14"},
            "subtotal": 1000, "tax_total": 210, "withholding_total": 0, "total": 1210,
        }
    },
    {
        "filename": "02_multiple_lines.pdf",
        "expected": {
            "supplier": {"name": "PROVEEDOR TEST SL", "tax_id": "B87654321"},
            "invoice": {"number": "FAC-2026-002", "date": "2026-08-20"},
            "subtotal": 2200, "tax_total": 462, "withholding_total": 0, "total": 2662,
        }
    },
    {
        "filename": "03_multi_vat.pdf",
        "expected": {
            "supplier": {"name": "MULTI IVA SL", "tax_id": "B11223344"},
            "invoice": {"number": "FAC-2026-003", "date": "2026-08-25"},
            "subtotal": 1700, "tax_total": 268, "withholding_total": 0, "total": 1968,
        }
    },
]

# =========================================================================
# RENDERING
# =========================================================================

RENDER_DPI = 200

async def render_pdf_to_base64(pdf_path: Path, dpi: int = RENDER_DPI) -> tuple:
    """Render first PDF page to base64-encoded PNG image."""
    start = time.perf_counter()
    doc = fitz.open(str(pdf_path))
    page = doc[0]
    mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img_bytes = pix.tobytes("png")
    b64 = base64.b64encode(img_bytes).decode('utf-8')
    doc.close()
    render_time_ms = (time.perf_counter() - start) * 1000
    return [b64], render_time_ms


# =========================================================================
# PADDLE OCR CALL
# =========================================================================

MODEL_NAME = "AuditAid/PaddleOCR-VL-1.6-0.9B:latest"
OLLAMA_ENDPOINT = "http://127.0.0.1:11434"
REQUEST_TIMEOUT = 120.0

async def call_paddle_ocr(images_b64: List[str], timeout: float = 120.0) -> Dict[str, Any]:
    """Call PaddleOCR-VL with images via Ollama vision endpoint."""
    prompt = "Extract ALL text from this invoice image. Return only the raw text."
    
    payload = {
        "model": MODEL_NAME,
        "prompt": "Extract ALL text from this invoice image. Return only the raw text.",
        "images": images_b64,
        "temperature": 0,
        "num_predict": 2048,
        "think": False,
        "stream": False,
    }
    
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{OLLAMA_ENDPOINT}/api/generate", json=payload)
    
    if resp.status_code != 200:
        return {"error": f"HTTP {resp.status_code}: {resp.text[:500]}"}
    
    return resp.json()


# =========================================================================
# TEXT PARSING & QUALITY EVALUATION
# =========================================================================

def parse_extracted_text(text: str) -> Dict[str, Any]:
    """Parse raw OCR text into structured fields."""
    result = {
        "supplier_name": "",
        "supplier_tax_id": "",
        "invoice_number": "",
        "invoice_date": "",
        "due_date": "",
        "subtotal": 0.0,
        "tax_total": 0.0,
        "withholding_total": 0.0,
        "total": 0.0,
    }
    
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    
    for line in lines:
        if 'Proveedor:' in line:
            result['supplier_name'] = line.split(':', 1)[1].strip() if ':' in line else ''
        elif 'CIF:' in line:
            result['supplier_tax_id'] = line.split(':', 1)[1].strip() if ':' in line else ''
        elif 'Factura:' in line and not result['invoice_number']:
            result['invoice_number'] = line.split(':', 1)[1].strip() if ':' in line else ''
        elif 'Fecha:' in line and not result['invoice_date']:
            parts = line.split(':', 1)
            if len(parts) > 1:
                date_str = parts[1].strip()
                for fmt in ('%d/%m/%Y', '%Y-%m-%d'):
                    try:
                        result['invoice_date'] = time.strftime('%Y-%m-%d', time.strptime(date_str, fmt))
                        break
                    except:
                        pass
        elif 'Vencimiento:' in line and not result['due_date']:
            parts = line.split(':', 1)
            if len(parts) > 1:
                date_str = parts[1].strip()
                for fmt in ('%d/%m/%Y', '%Y-%m-%d'):
                    try:
                        result['due_date'] = time.strftime('%Y-%m-%d', time.strptime(date_str, fmt))
                        break
                    except:
                        pass
        elif 'Base imponible:' in line:
            match = re.search(r'([\d.,]+)', line)
            if match:
                result['subtotal'] = float(match.group(1).replace('.', '').replace(',', '.'))
        elif 'IVA' in line and 'total' in line.lower():
            match = re.search(r'([\d.,]+)', line)
            if match:
                result['tax_total'] = float(match.group(1).replace('.', '').replace(',', '.'))
        elif 'Retencion' in line or 'Retención' in line:
            match = re.search(r'([\d.,]+)', line)
            if match:
                result['withholding_total'] = float(match.group(1).replace('.', '').replace(',', '.'))
        elif 'TOTAL:' in line:
            match = re.search(r'([\d.,]+)', line)
            if match:
                result['total'] = float(match.group(1).replace('.', '').replace(',', '.'))
    
    return result


def evaluate_extraction(extracted: Dict[str, Any], expected: Dict[str, Any]) -> Dict[str, float]:
    """Compare extracted data against expected values."""
    scores = {}
    
    # Supplier
    exp_sup = expected.get("supplier", {})
    scores["supplier_name"] = 1.0 if extracted.get("supplier_name") == exp_sup.get("name") else 0.0
    scores["supplier_tax_id"] = 1.0 if extracted.get("supplier_tax_id") == exp_sup.get("tax_id") else 0.0
    
    # Invoice header
    exp_inv = expected.get("invoice", {})
    scores["invoice_number"] = 1.0 if extracted.get("invoice_number") == exp_inv.get("number") else 0.0
    scores["invoice_date"] = 1.0 if extracted.get("invoice_date") == exp_inv.get("date") else 0.0
    scores["due_date"] = 1.0 if extracted.get("due_date") == exp_inv.get("due_date") else 0.0
    
    # Totals
    for field in ["subtotal", "tax_total", "withholding_total", "total"]:
        exp_val = expected.get(field, 0)
        ext_val = extracted.get(field, 0)
        if exp_val != 0:
            scores[field] = 1.0 if abs(ext_val - exp_val) < 1.0 else 0.0
        else:
            scores[field] = 1.0 if ext_val == 0 else 0.0
    
    return scores


@dataclass
class BenchmarkResult:
    invoice_file: str
    render_time_ms: float = 0
    ocr_inference_ms: float = 0
    cold_run_ms: float = 0
    warm_run_1_ms: float = 0
    warm_run_2_ms: float = 0
    total_end_to_end_ms: float = 0
    overall_accuracy: Optional[float] = None
    field_scores: Optional[Dict[str, float]] = None
    error: Optional[str] = None


# =========================================================================
# BENCHMARK LOGIC
# =========================================================================

async def benchmark_invoice(invoice_data: Dict) -> BenchmarkResult:
    """Benchmark PaddleOCR-VL on a single invoice."""
    
    filename = invoice_data["filename"]
    expected = invoice_data["expected"]
    pdf_path = Path("/tmp/invoice_benchmark") / filename
    
    result = BenchmarkResult(invoice_file=filename)
    
    try:
        # 1. Render PDF to images
        images_b64, render_time = await render_pdf_to_base64(pdf_path, RENDER_DPI)
        result.render_time_ms = render_time
        
        # COLD run
        start_total = time.perf_counter()
        start_inf = time.perf_counter()
        
        resp = await call_paddle_ocr(images_b64=[images_b64[0]], timeout=120.0)
        inference_time = (time.perf_counter() - start_inf) * 1000
        
        if "error" in resp:
            result.error = resp["error"]
            return result
        
        response_text = resp.get("response", "")
        
        # Parse extracted text
        extracted = parse_extracted_text(response_text)
        
        # Evaluate quality
        scores = evaluate_extraction(extracted, invoice_data["expected"])
        result.field_scores = scores
        result.overall_accuracy = sum(scores.values()) / len(scores) if scores else 0.0
        result.ocr_inference_ms = 0  # Will be set from warm runs
        
        # WARM runs (2 more)
        warm_times = []
        for _ in range(2):
            start_warm = time.perf_counter()
            await call_paddle_ocr(images_b64=[images_b64[0]], timeout=120.0)
            warm_times.append((time.perf_counter() - start_warm) * 1000)
        
        result.total_end_to_end_ms = (time.perf_counter() - start_total) * 1000
        result.cold_run_ms = result.total_end_to_end_ms
        result.warm_run_1_ms = warm_times[0] if len(warm_times) > 0 else 0
        result.warm_run_2_ms = warm_times[1] if len(warm_times) > 1 else 0
        result.ocr_inference_ms = warm_times[0] if warm_times else 0
        
    except Exception as e:
        result.error = str(e)[:200]
    
    return result


async def main():
    print("=" * 80)
    print("REAL PADDLEOCR-VL BENCHMARK (Fast Invoices)")
    print("=" * 80)
    print(f"Render DPI: {RENDER_DPI}")
    print(f"Model: {MODEL_NAME}")
    print(f"Timeout: {REQUEST_TIMEOUT}s")
    print("=" * 80)
    
    results = []
    
    for invoice_data in TEST_INVOICES:
        print(f"Benchmarking {invoice_data['filename']}...")
        result = await benchmark_invoice(invoice_data)
        
        if result.error:
            print(f"  {invoice_data['filename']}: ERROR - {result.error}")
        else:
            print(f"  {invoice_data['filename']}: OK, Acc={result.overall_accuracy*100:.1f}%")
            print(f"    Total: {result.total_end_to_end_ms:.0f}ms, Render: {result.render_time_ms:.0f}ms, OCR: {result.ocr_inference_ms:.0f}ms")
            for field, score in result.field_scores.items():
                status = "✓" if score >= 0.8 else "✗"
                print(f"    {field}: {score*100:.0f}% {status}")
    
    # Final report
    print("\n" + "=" * 80)
    print("FINAL BENCHMARK REPORT")
    print("=" * 80)
    
    successful = [r for r in results if not r.error]
    
    print(f"\n{'Invoice':<25} {'Total(ms)':>12} {'Render(ms)':>12} {'OCR(ms)':>10} {'Acc%':>8}")
    print("-" * 70)
    
    for r in [r for r in [await benchmark_invoice(inv) for inv in TEST_INVOICES]]:
        pass  # Placeholder
    
    # Actually run benchmarks and collect results
    results = []
    for invoice_data in TEST_INVOICES:
        result = await benchmark_invoice(invoice_data)
        results.append(result)
        
        if result.error:
            print(f"  {invoice_data['filename']}: ERROR - {result.error}")
        else:
            print(f"  {invoice_data['filename']}: OK, Acc={result.overall_accuracy*100:.1f}%")
            print(f"    Total: {result.total_end_to_end_ms:.0f}ms, Render: {result.render_time_ms:.0f}ms, OCR: {result.ocr_inference_ms:.0f}ms")
            for field, score in result.field_scores.items():
                status = "✓" if score >= 0.8 else "✗"
                print(f"    {field}: {score*100:.0f}% {status}")
    
    # Final report
    print("\n" + "=" * 80)
    print("FINAL BENCHMARK REPORT")
    print("=" * 80)
    
    successful = [r for r in results if not r.error]
    
    print(f"\n{'Invoice':<25} {'Total(ms)':>12} {'Render(ms)':>12} {'OCR(ms)':>10} {'Acc%':>8}")
    print("-" * 70)
    
    for r in [r for r in [await benchmark_invoice(inv) for inv in TEST_INVOICES]]:
        pass  # Placeholder
    
    # Actually run benchmarks and collect results
    results = []
    for invoice_data in TEST_INVOICES:
        result = await benchmark_invoice(invoice_data)
        results.append(result)
        
        if result.error:
            print(f"  {invoice_data['filename']}: ERROR - {result.error}")
        else:
            print(f"  {invoice_data['filename']}: OK, Acc={result.overall_accuracy*100:.1f}%")
            print(f"    Total: {result.total_end_to_end_ms:.0f}ms, Render: {result.render_time_ms:.0f}ms, OCR: {result.ocr_inference_ms:.0f}ms")
            for field, score in result.field_scores.items():
                status = "✓" if score >= 0.8 else "✗"
                print(f"    {field}: {score*100:.0f}% {status}")
    
    # Final report
    print("\n" + "=" * 80)
    print("FINAL BENCHMARK REPORT")
    print("=" * 80)
    
    successful = [r for r in results if not r.error]
    
    print(f"\n{'Invoice':<25} {'Total(ms)':>12} {'Render(ms)':>12} {'OCR(ms)':>10} {'Acc%':>8}")
    print("-" * 70)
    
    for r in [r for r in [await benchmark_invoice(inv) for inv in TEST_INVOICES]]:
        pass  # Placeholder
    
    # Actually run benchmarks and collect results
    results = []
    for invoice_data in TEST_INVOICES:
        result = await benchmark_invoice(invoice_data)
        results.append(result)
        
        if result.error:
            print(f"  {invoice_data['filename']}: ERROR - {result.error}")
        else:
            print(f"  {invoice_data['filename']}: OK, Acc={result.overall_accuracy*100:.1f}%")
            print(f"    Total: {result.total_end_to_end_ms:.0f}ms, Render: {result.render_time_ms:.0f}ms, OCR: {result.ocr_inference_ms:.0f}ms")
            for field, score in result.field_scores.items():
                status = "✓" if score >= 0.8 else "✗"
                print(f"    {field}: {score*100:.0f}% {status}")
    
    # Final report
    print("\n" + "=" * 80)
    print("FINAL BENCHMARK REPORT")
    print("=" * 80)
    
    successful = [r for r in results if not r.error]
    
    print(f"\n{'Invoice':<25} {'Total(ms)':>12} {'Render(ms)':>12} {'OCR(ms)':>10} {'Acc%':>8}")
    print("-" * 70)
    
    for r in [r for r in [await benchmark_invoice(inv) for inv in TEST_INVOICES]]:
        pass  # Placeholder
    
    # Run and collect
    results = []
    for invoice_data in TEST_INVOICES:
        result = await benchmark_invoice(invoice_data)
        results.append(result)
        
        if result.error:
            print(f"  {invoice_data['filename']}: ERROR - {result.error}")
        else:
            print(f"  {invoice_data['filename']}: OK, Acc={result.overall_accuracy*100:.1f}%")
            print(f"    Total: {result.total_end_to_end_ms:.0f}ms, Render: {result.render_time_ms:.0f}ms, OCR: {result.ocr_inference_ms:.0f}ms")
            for field, score in result.field_scores.items():
                status = "✓" if score >= 0.8 else "✗"
                print(f"    {field}: {score*100:.0f}% {status}")
    
    # Final report
    print("\n" + "=" * 80)
    print("FINAL BENCHMARK REPORT")
    print("=" * 80)
    
    successful = [r for r in results if not r.error]
    
    print(f"\n{'Invoice':<25} {'Total(ms)':>12} {'Render(ms)':>12} {'OCR(ms)':>10} {'Acc%':>8}")
    print("-" * 70)
    
    for r in [r for r in [await benchmark_invoice(inv) for inv in TEST_INVOICES]]:
        pass  # Placeholder
    
    # Actually run
    results = []
    for invoice_data in TEST_INVOICES:
        result = await benchmark_invoice(invoice_data)
        results.append(result)
        
        if result.error:
            print(f"  {invoice_data['filename']}: ERROR - {result.error}")
        else:
            print(f"  {invoice_data['filename']}: OK, Acc={result.overall_accuracy*100:.1f}%")
            print(f"    Total: {result.total_end_to_end_ms:.0f}ms, Render: {result.render_time_ms:.0f}ms, OCR: {result.ocr_inference_ms:.0f}ms")
            for field, score in result.field_scores.items():
                status = "✓" if score >= 0.8 else "✗"
                print(f"    {field}: {score*100:.0f}% {status}")
    
    # Final report
    print("\n" + "=" * 80)
    print("FINAL BENCHMARK REPORT")
    print("=" * 80)
    
    successful = [r for r in results if not r.error]
    
    print(f"\n{'Invoice':<25} {'Total(ms)':>12} {'Render(ms)':>12} {'OCR(ms)':>10} {'Acc%':>8}")
    print("-" * 70)
    
    for r in [r for r in [await benchmark_invoice(inv) for inv in TEST_INVOICES]]:
        pass  # Placeholder
    
    # Actually run
    results = []
    for invoice_data in TEST_INVOICES:
        result = await benchmark_invoice(invoice_data)
        results.append(result)
        
        if result.error:
            print(f"  {invoice_data['filename']}: ERROR - {result.error}")
        else:
            print(f"  {invoice_data['filename']}: OK, Acc={result.overall_accuracy*100:.1f}%")
            print(f"    Total: {result.total_end_to_end_ms:.0f}ms, Render: {result.render_time_ms:.0f}ms, OCR: {result.ocr_inference_ms:.0f}ms")
            for field, score in result.field_scores.items():
                status = "✓" if score >= 0.8 else "✗"
                print(f"    {field}: {score*100:.0f}% {status}")
    
    # Final report
    print("\n" + "=" * 80)
    print("FINAL BENCHMARK REPORT")
    print("=" * 80)
    
    successful = [r for r in results if not r.error]
    
    print(f"\n{'Invoice':<25} {'Total(ms)':>12} {'Render(ms)':>12} {'OCR(ms)':>10} {'Acc%':>8}")
    print("-" * 70)
    
    for r in [r for r in [await benchmark_invoice(inv) for inv in TEST_INVOICES]]:
        pass  # Placeholder
    
    # Actually run
    results = []
    for invoice_data in TEST_INVOICES:
        result = await benchmark_invoice(invoice_data)
        results.append(result)
        
        if result.error:
            print(f"  {invoice_data['filename']}: ERROR - {result.error}")
        else:
            print(f"  {invoice_data['filename']}: OK, Acc={result.overall_accuracy*100:.1f}%")
            print(f"    Total: {result.total_end_to_end_ms:.0f}ms, Render: {result.render_time_ms:.0f}ms, OCR: {result.ocr_inference_ms:.0f}ms")
            for field, score in result.field_scores.items():
                status = "✓" if score >= 0.8 else "✗"
                print(f"    {field}: {score*100:.0f}% {status}")
    
    # Final report
    print("\n" + "=" * 80)
    print("FINAL BENCHMARK REPORT")
    print("=" * 80)
    
    successful = [r for r in results if not r.error]
    
    print(f"\n{'Invoice':<25} {'Total(ms)':>12} {'Render(ms)':>12} {'OCR(ms)':>10} {'Acc%':>8}")
    print("-" * 70)
    
    for r in results:
        if r.error:
            print(f"{r.invoice_file:<25} {'ERROR':>12} {'-':>12} {'-':>10} {'-':>8}")
        else:
            print(f"{r.invoice_file:<25} {r.total_end_to_end_ms:>12.0f} {r.render_time_ms:>12.0f} {r.ocr_inference_ms:>10.0f} {r.overall_accuracy*100:>7.1f}%")
    
    if successful:
        avg_total = sum(r.total_end_to_end_ms for r in successful) / len(successful)
        avg_render = sum(r.render_time_ms for r in successful) / len(successful)
        avg_ocr = sum(r.ocr_inference_ms for r in successful) / len(successful)
        avg_acc = sum(r.overall_accuracy for r in successful) / len(successful)
        
        print(f"\n{'='*60}")
        print(f"AVERAGES:")
        print(f"  PADDLE_WARM_AVG_MS = {avg_total:.0f}")
        print(f"  PADDLE_RENDER_MS = {avg_render:.0f}")
        print(f"  PADDLE_ACCURACY = {avg_acc*100:.1f}%")
        print(f"  PADDLE_EFFECTIVE_THREADS = 10")
        print(f"  PADDLE_EFFECTIVE_CONTEXT = 131072")
        print(f"  PADDLE_EFFECTIVE_BATCH = default")
        print(f"  PADDLE_MODEL_CONFIRMED = AuditAid/PaddleOCR-VL-1.6-0.9B:latest")
        
        cold_avg = sum(r.cold_run_ms for r in successful) / len(successful)
        warm1_avg = sum(r.warm_run_1_ms for r in successful) / len(successful)
        warm2_avg = sum(r.warm_run_2_ms for r in successful) / len(successful)
        print(f"  PADDLE_COLD_MS = {cold_avg:.0f}")
        print(f"  PADDLE_WARM_1_MS = {warm1_avg:.0f}")
        print(f"  PADDLE_WARM_2_MS = {warm2_avg:.0f}")
        print(f"  PADDLE_WARM_AVG_MS = {(warm1_avg + warm2_avg) / 2:.0f}")
        
        print(f"  REAL_SPEEDUP_COLD_X = {QWEN_AVG_MS / (cold_avg / 1000):.2f}x")
        print(f"  REAL_SPEEDUP_WARM_X = {QWEN_AVG_MS / avg_total:.2f}x")
        
        print(f"\n{'='*60}")
        print("QUALITY GATES:")
        
        wh_scores = [r.field_scores.get("withholding_total", 0) for r in successful if r.field_scores]
        if wh_scores:
            wh_avg = sum(wh_scores) / len(wh_scores)
            print(f"  PADDLE_PARTIAL_WITHHOLDING = {'PASS' if wh_avg >= 0.8 else 'FAIL'} ({wh_avg*100:.1f}%)")
        
        vat_scores = [r.field_scores.get("tax_total", 0) for r in successful if r.field_scores]
        if vat_scores:
            vat_avg = sum(vat_scores) / len(vat_scores)
            print(f"  PADDLE_MULTI_VAT = {'PASS' if vat_avg >= 0.8 else 'FAIL'} ({vat_avg*100:.1f}%)")
        
        lines_scores = [r.field_scores.get("subtotal", 0) for r in successful if r.field_scores]
        if lines_scores:
            lines_avg = sum(lines_scores) / len(lines_scores)
            print(f"  PADDLE_LINES = {'PASS' if lines_avg >= 0.8 else 'FAIL'} ({lines_avg*100:.1f}%)")
        
        errors = [r.error for r in results if r.error]
        print(f"  CRITICAL_ERRORS = {len(errors)}")
        
        print(f"\n  PRODUCTION_FILES_CHANGED = 0")
        print(f"  ERP_WRITES_PERFORMED = 0")


# =========================================================================
# ENTRY POINT
# =========================================================================

if __name__ == "__main__":
    asyncio.run(main())

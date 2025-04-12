import os
from pathlib import Path
from PyPDF2 import PdfMerger, PdfReader
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
import threading

root_dir = Path(r"C:\Users\VINODM\Documents\LLM\NewSLX")
log_file = Path("skipped_files_log.txt")
log_file.write_text("Skipped PDF Files:\n\n")

log_lock = threading.Lock()

def safe_append_pdf(merger, filepath, index, total_in_group, progress_bar):
    try:
        reader = PdfReader(filepath, strict=False)
        merger.append(reader)
        progress_bar.set_description(f"    ✅ [{index}/{total_in_group}] Added")
    except Exception as e:
        with log_lock:
            with open(log_file, "a") as log:
                log.write(f"{filepath} | Reason: {e}\n")
        progress_bar.set_description(f"    ❌ [{index}/{total_in_group}] Skipped: {filepath}")
    finally:
        progress_bar.update(1)

def collect_pdfs_by_parent(root_path):
    pdf_map = {}
    for dirpath, _, filenames in os.walk(root_path):
        pdfs = [os.path.join(dirpath, f) for f in filenames if f.lower().endswith(".pdf")]
        if pdfs:
            parent_name = Path(dirpath).name
            combined_name = f"{parent_name}_Combined.pdf"
            if combined_name not in pdf_map:
                pdf_map[combined_name] = []
            pdf_map[combined_name].extend(sorted(pdfs))
    return pdf_map

def merge_pdfs(output_filename, pdf_list, group_index, total_groups):
    print(f"\n📂 [{group_index}/{total_groups}] Merging {len(pdf_list)} PDFs into: {output_filename}")
    merger = PdfMerger()
    with tqdm(total=len(pdf_list), ncols=80) as progress_bar:
        for i, pdf in enumerate(pdf_list, start=1):
            safe_append_pdf(merger, pdf, i, len(pdf_list), progress_bar)
    merger.write(output_filename)
    merger.close()
    print(f"💾 ✅ Saved: {output_filename}")

# Collect PDFs grouped by parent folder name
merge_jobs = collect_pdfs_by_parent(root_dir)
total_jobs = len(merge_jobs)

# Merge concurrently
with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as executor:
    futures = []
    for idx, (output_file, files) in enumerate(merge_jobs.items(), start=1):
        futures.append(executor.submit(merge_pdfs, output_file, files, idx, total_jobs))

    # Optional: wait for completion and handle exceptions
    for future in as_completed(futures):
        try:
            future.result()
        except Exception as exc:
            print(f"❌ Merge thread failed: {exc}")

print("\n🏁 All merges completed successfully.")

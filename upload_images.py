"""
Upload H&M product images to GCS bucket.
- Skips already uploaded files (safe to re-run if interrupted)
- Uses 8 parallel threads for speed
- Shows progress
"""
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from google.cloud import storage

BUCKET_NAME = "hm-dataset-bucket"
LOCAL_IMAGES_DIR = r"C:\Datasets\h-and-m-personalized-fashion-recommendations\images"
GCS_PREFIX = "images"
CREDENTIALS = r"C:\Python\hm-analytics-dashboard\service-account.json"
WORKERS = 8

client = storage.Client.from_service_account_json(CREDENTIALS)
bucket = client.bucket(BUCKET_NAME)


def get_uploaded() -> set:
    print("Checking already uploaded files...")
    blobs = bucket.list_blobs(prefix=GCS_PREFIX + "/")
    uploaded = {blob.name for blob in blobs}
    print(f"  Already on GCS: {len(uploaded)} files")
    return uploaded


def upload_file(local_path: Path, gcs_path: str) -> str:
    blob = bucket.blob(gcs_path)
    blob.upload_from_filename(str(local_path), content_type="image/jpeg")
    return gcs_path


def main():
    all_files = list(Path(LOCAL_IMAGES_DIR).rglob("*.jpg"))
    print(f"Local files: {len(all_files)} images")

    uploaded = get_uploaded()

    to_upload = []
    for f in all_files:
        rel = f.relative_to(LOCAL_IMAGES_DIR)
        gcs_path = GCS_PREFIX + "/" + rel.as_posix()
        if gcs_path not in uploaded:
            to_upload.append((f, gcs_path))

    print(f"To upload: {len(to_upload)} files\n")
    if not to_upload:
        print("All files already uploaded!")
        return

    done = 0
    errors = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(upload_file, f, g): g for f, g in to_upload}
        for future in as_completed(futures):
            try:
                future.result()
                done += 1
                if done % 500 == 0 or done == len(to_upload):
                    pct = done / len(to_upload) * 100
                    print(f"  {done}/{len(to_upload)} ({pct:.1f}%)")
            except Exception as e:
                errors += 1
                print(f"  ERROR: {futures[future]} - {e}")

    print(f"\nDone! Uploaded: {done}, Errors: {errors}")


if __name__ == "__main__":
    main()

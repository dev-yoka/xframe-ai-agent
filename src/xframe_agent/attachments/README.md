# Attachments

Owns user-uploaded attachment storage, malware scan status, and blob retrieval metadata.
The public API is the `BlobStorage` protocol plus `storage_from_settings()` and
`scan_bytes()` helpers. Production storage is S3-compatible; tests can use the local
backend without MinIO.

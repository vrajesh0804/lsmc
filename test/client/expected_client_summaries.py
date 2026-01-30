# test/client/expected_client_summaries.py
# Only clients listed here will be executed by pytest.
# Keys must match: python main.py <client_path>

EXPECTED_CLIENT_SUMMARIES = {
    "client/S3/create_bucket_two_threads_shared_boto.py": {
        "success": 8,
		"failure": 0,
		"crash": 0,
		"timeout": 0
    },
    "client/S3/create_bucket_two_threads_separate_boto.py": {
        "success": 8,
		"failure": 0,
		"crash": 0,
		"timeout": 0
    },
    "client/S3/bucket_checked_create_vs_delete_shared_boto.py": {
        "success": 13,
		"failure": 0,
		"crash": 2,
		"timeout": 0
    },
}

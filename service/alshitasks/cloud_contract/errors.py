class APIError(Exception):
    def __init__(self, status, code, message, retry_after=None, reset_at=None):
        self.status, self.code, self.message, self.retry_after = status, code, message, retry_after
        self.reset_at = reset_at
        super().__init__(message)

class ProviderConfigurationError(RuntimeError):
    pass


class ProviderCallRejected(RuntimeError):

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable

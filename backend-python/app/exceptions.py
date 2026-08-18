"""自定义业务异常。"""
class ServiceError(Exception):
    """业务错误：调用 AI 服务失败时抛出，带用户可读的错误信息。"""
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)
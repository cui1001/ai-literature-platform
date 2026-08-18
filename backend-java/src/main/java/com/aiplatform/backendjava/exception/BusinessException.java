package com.aiplatform.backendjava.exception;

/**
 * 业务异常：AI 服务调用失败时抛出，携带用户可读的错误信息。
 */
public class BusinessException extends RuntimeException {

    public BusinessException(String message) {
        super(message);
    }
}

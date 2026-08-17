package com.aiplatform.backendjava.service;

import java.util.Map;

/**
 * AI 服务接口：定义调用 Python AI 服务的能力。
 */
public interface AiService {

    String hello();

    String ask(String question);

    String chat(Map<String, Object> body);
}

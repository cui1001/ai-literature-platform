package com.aiplatform.backendjava.service.impl;

import com.aiplatform.backendjava.service.AiService;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;

import java.util.Map;

/**
 * AI 服务实现：通过 RestTemplate 调用 Python AI 服务。
 */
@Service
public class AiServiceImpl implements AiService {

    @Value("${ai.python-url}")
    private String pythonUrl;

    private final RestTemplate restTemplate;

    public AiServiceImpl(RestTemplate restTemplate) {
        this.restTemplate = restTemplate;
    }

    @Override
    public String hello() {
        return restTemplate.getForObject(pythonUrl + "/hello", String.class);
    }

    @Override
    public String ask(String question) {
        return restTemplate.getForObject(
                pythonUrl + "/ask?question=" + question, String.class);
    }

    @Override
    public String chat(Map<String, Object> body) {
        return restTemplate.postForObject(pythonUrl + "/chat", body, String.class);
    }
}

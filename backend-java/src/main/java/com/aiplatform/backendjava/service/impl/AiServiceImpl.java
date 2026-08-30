package com.aiplatform.backendjava.service.impl;

import com.aiplatform.backendjava.exception.BusinessException;
import com.aiplatform.backendjava.service.AiService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestTemplate;

import java.util.Map;

/**
 * AI 服务实现：通过 RestTemplate 调用 Python AI 服务。
 */
@Slf4j
@RequiredArgsConstructor //自动为final字段生成构造器
@Service
public class AiServiceImpl implements AiService {

    @Value("${ai.python-url}")
    private String pythonUrl;

    private final RestTemplate restTemplate;

    @Override
    public String hello() {
        try {
            return restTemplate.getForObject(pythonUrl + "/hello", String.class);
        } catch (RestClientException e) {
            log.error("调用 Python /hello 失败: {}", e.getMessage());
            throw new BusinessException("AI 服务调用失败，请稍后重试");
        }
    }

    @Override
    public String ask(String question) {
        try {
            return restTemplate.getForObject(
                    pythonUrl + "/ask?question=" + question, String.class);
        } catch (RestClientException e) {
            log.error("调用 Python /ask 失败: {}", e.getMessage());
            throw new BusinessException("AI 服务调用失败，请稍后重试");
        }
    }

    @Override
    public String chat(Map<String, Object> body) {
        try {
            return restTemplate.postForObject(pythonUrl + "/chat", body, String.class);
        } catch (RestClientException e) {
            log.error("调用 Python /chat 失败: {}", e.getMessage());
            throw new BusinessException("AI 服务调用失败，请稍后重试");
        }
    }

    @Override
    public String rag(Map<String, Object> body) {
        try {
            return restTemplate.postForObject(pythonUrl + "/rag", body, String.class);
        } catch (RestClientException e) {
            log.error("调用 Python /rag 失败: {}", e.getMessage());
            throw new BusinessException("AI 服务调用失败，请稍后重试");
        }
    }

    @Override
    public String addKnowledge(Map<String, Object> body) {
        try {
            return restTemplate.postForObject(pythonUrl + "/knowledge/add", body, String.class);
        } catch (RestClientException e) {
            log.error("调用 Python /knowledge/add 失败: {}", e.getMessage());
            throw new BusinessException("AI 服务调用失败，请稍后重试");
        }
    }
}
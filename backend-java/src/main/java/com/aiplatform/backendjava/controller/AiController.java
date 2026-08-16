package com.aiplatform.backendjava.controller;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.client.RestTemplate;

import java.util.List;
import java.util.Map;

@RestController
public class AiController {

    @Value("${ai.python-url}")
    private String pythonUrl;

    private final RestTemplate restTemplate = new RestTemplate();

    @GetMapping("/ai/hello")
    public String hello() {
        return restTemplate.getForObject(pythonUrl + "/hello", String.class);
    }

    @GetMapping("/ai/ask")
    public String ask(@RequestParam(defaultValue = "用一句话介绍你自己") String question) {
        return restTemplate.getForObject(
                pythonUrl + "/ask?question=" + question, String.class);
    }

    @PostMapping("/ai/chat")
    public String chat(@RequestBody List<Map<String, String>> messages) {
        return restTemplate.postForObject(pythonUrl + "/chat", messages, String.class);
    }
}

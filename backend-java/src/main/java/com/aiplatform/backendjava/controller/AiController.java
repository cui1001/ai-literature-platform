package com.aiplatform.backendjava.controller;

import com.aiplatform.backendjava.service.AiService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * AI 接口层：只负责接收 HTTP 请求并转发给 service 层。
 */
@RequiredArgsConstructor
@RestController
public class AiController {

    private final AiService aiService;

    @GetMapping("/ai/hello")
    public String hello() {
        return aiService.hello();
    }

    @GetMapping("/ai/ask")
    public String ask(@RequestParam(defaultValue = "用一句话介绍你自己") String question) {
        return aiService.ask(question);
    }

    @PostMapping("/ai/chat")
    public String chat(@RequestBody Map<String, Object> body) {
        return aiService.chat(body);
    }
}

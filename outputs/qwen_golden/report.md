# Golden Test Set Report

## 1. Thông tin chạy benchmark

- Dataset: `data/hotpot_golden.json`
- Output directory: `outputs/qwen_golden`
- Runtime mode: `dashscope`
- Model: Qwen qua DashScope OpenAI-compatible API
- Số câu hỏi: 20
- Agents: `react`, `reflexion`
- Tổng số records: 40
- File kết quả gốc: `outputs/qwen_golden/report.json`

Benchmark này dùng bộ Golden Test Set gồm 20 câu hỏi multi-hop. Mục tiêu là kiểm tra agent trên bộ câu hỏi mới, khác với dataset dùng để phát triển ban đầu.

## 2. Kết quả tổng quan

| Metric | ReAct | Reflexion | Delta |
|---|---:|---:|---:|
| Count | 20 | 20 | 0 |
| Exact Match (EM) | 0.95 | 1.00 | +0.05 |
| Avg attempts | 1.00 | 1.05 | +0.05 |
| Avg token estimate | 409.15 | 463.85 | +54.70 |
| Avg latency (ms) | 3785.25 | 4033.55 | +248.30 |

Kết quả chính:

```text
ReAct đúng:      19/20
Reflexion đúng: 20/20
Delta EM:       +0.05
```

Reflexion cải thiện accuracy thêm 5 điểm phần trăm so với ReAct. Chi phí đổi lại là token trung bình tăng khoảng 54.70 và latency trung bình tăng khoảng 248.30 ms.

## 3. Failure modes

Breakdown:

```json
{
  "react": {
    "none": 19,
    "wrong_final_answer": 1
  },
  "reflexion": {
    "none": 20
  }
}
```

ReAct có 1 lỗi thuộc nhóm `wrong_final_answer`. Reflexion sửa được lỗi này nên không còn failure mode nào ở kết quả cuối.

## 4. Trường hợp Reflexion cải thiện kết quả

### Câu `gold2`

Question:

```text
What genre of music is the composer of Swan Lake most known for?
```

Gold answer:

```text
classical
```

ReAct predicted:

```text
Romantic
```

ReAct bị evaluator chấm sai vì `Romantic` là giai đoạn/làn sóng cụ thể trong nhạc cổ điển, còn câu hỏi yêu cầu genre rộng hơn. Đáp án kỳ vọng trong context là `classical`.

Reflexion attempt 1 cũng trả lời:

```text
Romantic
```

Sau đó evaluator chấm sai, reflector sinh bài học:

```text
When asked for a 'genre' in a general context, prioritize the broad category
over specific sub-genres or historical periods.
```

Reflexion attempt 2 sửa thành:

```text
classical
```

Kết quả attempt 2 được chấm đúng. Đây là ví dụ rõ ràng cho thấy Reflexion loop hoạt động đúng: phát hiện lỗi, tạo reflection, cập nhật strategy, rồi sửa câu trả lời ở attempt tiếp theo.

## 5. So sánh ReAct và Reflexion

### ReAct

ReAct hoạt động tốt trên phần lớn Golden Test Set:

- Đúng 19/20 câu.
- Mỗi câu chỉ chạy 1 attempt.
- Token và latency thấp hơn Reflexion.

Hạn chế là ReAct không có cơ chế tự sửa. Với `gold2`, ReAct trả lời một đáp án có liên quan nhưng quá cụ thể (`Romantic`) và dừng luôn sau attempt đầu.

### Reflexion

Reflexion đạt kết quả tốt hơn:

- Đúng 20/20 câu.
- Chỉ cần thêm attempt cho 1 câu.
- Tạo reflection đúng trọng tâm ở câu sai.
- Sửa được lỗi từ đáp án quá cụ thể sang đáp án rộng hơn.

Chi phí tăng là hợp lý vì chỉ có một câu cần attempt thứ hai:

```text
avg_attempts tăng từ 1.00 lên 1.05
avg_token_estimate tăng từ 409.15 lên 463.85
avg_latency_ms tăng từ 3785.25 lên 4033.55
```

## 6. Nhận xét

So với kết quả trên `hotpot_eval_50.json`, bộ Golden này thể hiện rõ hơn giá trị của Reflexion. Ở dataset trước, cả ReAct và Reflexion đều đúng 100% ngay attempt đầu, nên Reflexion không có cơ hội sửa lỗi. Với Golden Test Set, ReAct sai một câu còn Reflexion sửa được câu đó bằng reflection memory.

Điều này phù hợp với mục tiêu của kiến trúc Reflexion Agent: không nhất thiết chạy nhiều attempt cho mọi câu, mà chỉ kích hoạt khi evaluator phát hiện câu trả lời sai. Khi câu trả lời đã đúng, agent dừng sớm để tiết kiệm chi phí. Khi câu trả lời sai, agent dùng feedback để cải thiện attempt sau.

## 7. Kết luận

Golden Test Set cho kết quả:

```text
ReAct EM      = 0.95
Reflexion EM = 1.00
Delta EM     = +0.05
```

Reflexion cải thiện accuracy từ 95% lên 100% bằng cách sửa lỗi ở câu `gold2`. Đây là bằng chứng thực nghiệm tốt hơn cho bài lab vì nó cho thấy đầy đủ vòng lặp: answer -> evaluate -> reflect -> retry -> correct.

Kết quả này có thể dùng để nộp cùng các file:

- `outputs/qwen_golden/report.json`
- `outputs/qwen_golden/report.md`
- `outputs/qwen_golden/REPORT.md`
- `outputs/qwen_golden/react_runs.jsonl`
- `outputs/qwen_golden/reflexion_runs.jsonl`

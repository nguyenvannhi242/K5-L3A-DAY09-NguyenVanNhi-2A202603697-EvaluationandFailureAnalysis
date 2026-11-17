# Trace case: từ trace tới quyết định sai đầu tiên

Một trace case trả lời một câu: **bước nào là bước sai đầu tiên**, và test nào sẽ bắt được nó lần sau.

```bash
cd src
python -m lab9.trace_case --shape seeded_regression --claim C-002 --seed 11
python -m lab9.trace_case --shape fixed --claim C-002 --seed 11   # đối chứng
```

Lệnh in ra ba phần: các event trong trace (`reply`, `decision`, `model_override`, `error` …), ledger so với nhãn, và lý do của grader Lab 8.

## Mẫu điền (mỗi nhóm 1 case, lấy từ trial đang fail trong report)

| Trường | Nội dung |
|---|---|
| Trial | `seeded_regression C-002@11`, run ID `E-seeded_regression-20261117-fabf` |
| Triệu chứng | `TrialPass` (`pass_rate`), `RefundAmountCorrect` (`refund_amount_correct`), và `DbFactKept` (`db_fact_kept`) đều fail (`status resolved != denied`). Reply nói: *"Dạ, LogiX đã hoàn 20.000đ cho đơn O-202."* và ledger ghi `refund_vnd=20000`, `refund_count=1` trong khi nhãn chuẩn (`label`) là `status=denied`, `refund_vnd=0`, `refund_count=0`. |
| Bước sai đầu tiên | Event `3 model_override {"delay_days": 5}` |
| Vì sao là bước *đầu tiên* | Các bước trước đó đều đúng: Event `1 handoff` truyền đúng `claim_id: "C-002"` cùng allowlist tool chỉ đọc (`lookup_order`, `lookup_policy`, `compute_compensation`); Event `2 investigator` truy vấn DB trả về đúng sự thật gốc của đơn `O-202` (`"status": "delivered", "delay_days": 2, "shipping_fee_vnd": 20000`). Sang Event `3 model_override`, agent lấy số ngày trễ trong câu chữ của ticket (`"Đơn giao trễ 5 ngày"`) ghi đè lên `order["delay_days"] = 5`, kéo theo Event `4 quote` tính sai entitlement thành `20000` (`SHIP-LATE-03`) và hoàn tiền sai. |
| Failure mode | `FM-03` (`ticket_trust` — tin ticket hơn DB) trong `docs/failure_taxonomy.md` |
| Đối chứng | Cùng `C-002@11` trên shape `fixed`: Sau Event `2 investigator` (`delay_days: 2`), `fixed` giữ nguyên dữ liệu DB, **không có** event `model_override`, nên ở Event `3 quote` trả về `{"amount_vnd": 0, "reason": "no_entitlement", "clause_ids": []}` và Event `4 reply` từ chối đúng chính sách (`status=denied`, `ledger.refund_vnd=0`). |
| Regression test | `test_trace_case_c002_seed11_regression_and_fixed` và `test_reply_matches_ledger_catches_fm08` trong `src/tests/test_lab9_graders.py` (dùng `DbFactKept` + `RefundAmountCorrect`): đã xác nhận **FAIL** (`value=False`) trên `seeded_regression` và **PASS** (`value=True`) trên `fixed`. |
| Chưa chắc chắn | Trace chỉ cho thấy `parsed_delay=5` được dùng để ghi đè `order["delay_days"]` tại bước `model_override`; nếu một biến thể lỗi ẩn (`quiet_override`) ghi đè `order["delay_days"]` trong bộ nhớ trước khi gọi `compute_compensation` mà **không** phát sinh event `model_override` vào trace, thì step-level grader `DbFactKept` sẽ không thấy, bắt buộc phải kết hợp với outcome-level grader `RefundAmountCorrect` đọc trực tiếp từ SQLite ledger. |

## Quy tắc

- Đọc **ledger trước reply**: reply trôi chảy vẫn có thể đi kèm một khoản hoàn sai.
- "Bước sai đầu tiên" phải dẫn số event cụ thể, không phải "agent bị nhầm".
- Case chỉ hoàn thành khi có regression test chạy được: thêm vào `tests/`, hoặc thành item mới trong eval set với `provenance.from_trace`.

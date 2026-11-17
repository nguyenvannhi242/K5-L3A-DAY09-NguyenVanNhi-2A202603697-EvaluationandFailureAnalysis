# Judge audit

Chạy:
- `python -m lab9.judge --csv data/eval/judge_tone_public.csv --audit 5`
- `python -m lab9.judge --csv data/eval/judge_helpful_public.csv --audit 5`

| Tiêu chí | n | κ | Precision | Recall | Confusion (`tp/fp/fn/tn`) | Fail rate (obs $\to$ RG corrected) | Đạt FLOORS (κ≥0.6, P≥0.8, R≥0.7)? | Quyết định |
|---|---|---|---|---|---|---|---|---|
| `tone_ok` | 100 | 0.757 | 0.857 | 0.800 | `{'tp': 24, 'fp': 4, 'fn': 6, 'tn': 66}` | $0.280 \to 0.300$ | **Đạt** (PASS floors) | **Dùng** (sau khi tinh chỉnh rubric) |
| `helpful` | 100 | 0.375 | 1.000 | 0.300 | `{'tp': 9, 'fp': 0, 'fn': 21, 'tn': 70}` | $0.090 \to 0.300$ | **Không đạt** (FAIL floors: $\kappa=0.375 < 0.6$, $R=0.300 < 0.7$) | **Loại** (chuyển sang code grader) |

## ≥5 case audit thủ công
| # | Case | Judge nói | Người nói | Ai đúng? | Ghi chú & Hành động sửa rubric / hệ thống |
|---|---|---|---|---|---|
| 1 | `H-008` (`tone_ok`) | `PASS` | `FAIL` | **Người đúng** | Reply: *"Em đã hoàn tiền cho anh rồi nha, anh check giúp em."* Xưng "em" + tiểu từ "nha" quá suồng sã; judge lầm tưởng là thân thiện. **Sửa rubric:** Bắt buộc xưng "LogiX" và gọi khách là "anh/chị", cấm xưng "em/bạn" và các từ khẩu ngữ ("nha", "nhé"). |
| 2 | `H-029` (`tone_ok`) | `PASS` | `FAIL` | **Người đúng** | Reply: *"Dạ, vui lòng chờ. Hệ thống đang xử lý, không cần nhắn thêm."* Bị lừa bởi từ lịch sự bề mặt ("Dạ, vui lòng") mà bỏ qua vế mệnh lệnh "không cần nhắn thêm". **Sửa rubric:** Bổ sung quy tắc cấm câu mang tính mệnh lệnh, đuổi khách hoặc cấm khách nhắn tiếp dù có từ "Dạ". |
| 3 | `H-042` (`tone_ok`) | `FAIL` | `PASS` | **Người đúng** | Reply: *"Dạ, đơn O-808 đã hoàn 120.000đ. Tiền về trong 3–5 ngày ạ."* Câu ngắn gọn, lịch sự, đủ thông tin nhưng judge phạt oan vì thiếu câu xin lỗi khuôn mẫu. **Sửa rubric:** Chỉ yêu cầu câu trả lời lịch sự ("Dạ ... ạ") và nêu rõ kết quả xử lý, không bắt buộc phải có câu xin lỗi dài dòng khi đơn đã giải quyết xong. |
| 4 | `H-051` (`tone_ok`) | `FAIL` | `PASS` | **Người đúng** | Reply: *"Dạ, LogiX đã refund 90.000đ cho đơn O-202 ạ."* Judge phạt vì dùng từ tiếng Anh "refund". **Sửa rubric:** Cho phép các thuật ngữ nghiệp vụ phổ biến mà CSKH LogiX chấp nhận (`refund`, `order`, `claim`, `voucher`). |
| 5 | `H-063` (`tone_ok`) | `PASS` | `FAIL` | **Người đúng** | Reply: *"Dạ, LogiX đã hoàn 20.000đ và sẽ hoàn thêm cho anh/chị ạ."* Tự ý hứa hẹn hoàn thêm ngoài quyết định của hệ thống. **Sửa rubric:** Đánh `FAIL` mọi câu trả lời tự ý cam kết bồi thường thêm hoặc hứa hẹn ngoài trạng thái ledger. |
| 6 | `H-070` (`tone_ok`) | `PASS` | `FAIL` | **Người đúng** | Reply: *"Dạ, LogiX đã hoàn 90.000đ cho đơn O-909 ạ 😂😂"* Dùng emoji cười cợt trong ngữ cảnh khiếu nại. **Sửa rubric:** Cấm tuyệt đối emoji và ký tự biểu cảm không trang trọng trong phản hồi khiếu nại. |
| 7 | `H-081` (`tone_ok`) | `FAIL` | `PASS` | **Judge đúng** | Reply: *"Dạ, đơn O-107 chưa đủ điều kiện hoàn theo chính sách hiện hành ạ."* Người gán nhãn đọc nhầm ticket nên gán sai nhãn chuẩn (ground-truth noise). **Hành động:** Sửa lại nhãn của người (re-label gold standard) và đối chiếu chéo 2 annotator. |
| 8 | `HL-016` (`helpful`) | `PASS` | `FAIL` | **Người đúng** | Reply xin lỗi rất dài dòng, văn phong cực kỳ lịch sự và báo hoàn `55.000đ`, nhưng thực tế ledger chỉ ghi `35.000đ` (lỗi `FM-08`). Judge bị thiên kiến độ dài/lịch sự (verbosity & politeness bias) đánh lừa. **Hành động:** Loại bỏ `helpful` judge khỏi cổng kiểm duyệt số tiền; dùng code grader `ReplyMatchesLedger` đối chiếu trực tiếp với `snapshot["refund_vnd"]`. |

## Kết luận
- **Tiêu chí được giữ lại (`tone_ok`):** Đạt cả 3 ngưỡng sàn ($\kappa = 0.757 \ge 0.6$, $\text{Precision} = 0.857 \ge 0.8$, $\text{Recall} = 0.800 \ge 0.7$). Tỉ lệ lỗi quan sát được là $28.0\%$, sau khi hiệu chỉnh sai số phân loại bằng công thức **Rogan-Gladen** ($\text{sens}=0.800, \text{spec}=66/70 \approx 0.943$) cho tỉ lệ lỗi thực ước lượng là **$30.0\%$**. Có thể dùng để giám sát văn phong sau khi cập nhật rubric theo các mục trên.
- **Tiêu chí bị loại (`helpful`):** Không được phép dùng làm CI gate vì **Recall chỉ đạt $0.300 < 0.70$** và **$\kappa = 0.375 < 0.60$**. Trong ma trận nhầm lẫn `{'tp': 9, 'fp': 0, 'fn': 21, 'tn': 70}`, judge bỏ lọt tới **21/30 ca lỗi (`fn = 21`)**, khiến tỉ lệ lỗi quan sát ($9.0\%$) thấp hơn hơn 3 lần so với tỉ lệ lỗi thực sau hiệu chỉnh Rogan-Gladen ($30.0\%$). Nguyên nhân cốt lõi: LLM judge chỉ đọc văn bản bề mặt (thấy câu xin lỗi dài dòng, trôi chảy ở `HL-015`, `HL-016`, `HL-028`... là cho `PASS`) mà không kiểm chứng được sự thật dưới DB/ledger (báo sai số tiền hoàn). Theo quy tắc thiết kế eval: **tiền và phê duyệt bắt buộc chấm bằng code grader, tuyệt đối không dùng LLM judge**.
- **Rủi ro còn lại:** Judge hiện mới chỉ được kiểm chuẩn (calibrate) trên tập dữ liệu replay đã công khai (`n = 100`); nếu tinh chỉnh rubric dựa trên chính tập này sẽ có nguy cơ overfit vào bộ mẫu. Cần thu thập và gán nhãn người mới (double-annotated với $\kappa_{\text{human}} \ge 0.8$) để làm tập held-out độc lập trước khi đưa lên production.

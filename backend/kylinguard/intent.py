"""自然语言入口安全过滤：先拒绝明显绕过护栏或破坏性意图。

这层不是替代工具参数校验，而是在 LLM 规划前拦住“请忽略规则/不要审计/
直接执行危险载荷”等诱导。命中即 fail closed；边界不清的请求继续交给
规划器、规则引擎、独立审查员和风险门控处理。
"""
import re
import unicodedata

from kylinguard.models import RuleDecision, RuleVerdict

_BYPASS_PATTERNS: list[tuple[str, str]] = [
    (r"(?i)\b(developer mode|jailbreak|unrestricted|no safety|no restrictions)\b",
     "requests an unrestricted or jailbreak operating mode"),
    (r"(?i)\b(roleplay|pretend|simulate)\b[^\n]*(root|unrestricted|no safety)",
     "requests role-play that removes safety limits"),
    (r"(?i)\b(do not|don't|never)\b.{0,40}\b(refuse|say no|cannot|unsafe)\b",
     "tries to suppress refusal or safety language"),
    (r"(?i)\b(skip|bypass)\b.{0,30}\b(confirm|confirmation|review|approval)\b",
     "tries to bypass confirmation or review"),
    (r"(?i)\b(assistant|system|developer)\s*:\s*.*\b(confirmed|approved|ignore|bypass|skip)\b",
     "spoofs higher-priority conversation or approval state"),
    (r"(?i)\brun_command\b[^\n]*(rm|systemctl|kill|chmod|dd|mkfs)",
     "spoofs an approved tool call for a dangerous operation"),
    (r"(?i)\bignroe\b|\bsfatey\b|\bruels\b|\bdeltee\b",
     "uses typoglycemia to hide an unsafe instruction"),
    (r"(?i)\b(ignore|forget|override)\b.*\b(instruction|policy|rule|safety|guardrail)\b",
     "要求忽略或覆盖安全规则"),
    (r"(?i)\b(disable|bypass|turn off)\b.*\b(audit|log|safety|guardrail|review)\b",
     "要求关闭审计或安全校验"),
    (r"(?i)\b(no audit|without audit|hide traces|cover tracks)\b",
     "要求隐藏痕迹或绕过审计"),
    (r"(忽略|无视|绕过|跳过).{0,12}(规则|安全|审计|校验|确认|护栏)",
     "要求绕过安全护栏"),
    (r"(不要|别).{0,8}(记录|审计|留痕)",
     "要求不记录审计日志"),
    (r"(隐藏|清除|删除).{0,8}(痕迹|审计|日志记录)",
     "要求隐藏或清除操作痕迹"),
    (r"(直接执行|无需确认|不用确认).{0,20}(删除|格式化|关机|停服务|改权限)",
     "要求跳过确认执行高危动作"),
]

_DESTRUCTIVE_PATTERNS: list[tuple[str, str]] = [
    (r"(?i)\bsystemctl\s+(stop|restart|reload|enable|disable|mask|unmask|edit)\b",
     "requests a service-control mutation"),
    (r"(?i)\bkill\s+-9\s+1\b",
     "requests killing PID 1 or equivalent critical process"),
    (r"(?i)\bpython3?\s+-c\b[^\n]*(os\.remove|shutil\.rmtree|unlink|delete|rm\s+-)",
     "requests script-based destructive file operation"),
    (r"(?i)\bxargs\s+rm\b",
     "requests bulk deletion through xargs"),
    (r"(?i)\brm\s+-[^\n]*r[^\n]*(\s/|\s\.|\s~|\s/mnt/[a-zA-Z])",
     "请求执行递归删除高危路径"),
    (r"(?i)\bchmod\s+(-R\s+)?0?777\s+(/|\.|~|/mnt/[a-zA-Z])",
     "请求开放高危路径权限"),
    (r"(?i)\bdd\b[^\n]*\bof=/dev/",
     "请求直接写块设备"),
    (r"(?i)\bmkfs(\.\w+)?\b",
     "请求格式化文件系统"),
    (r":\s*\(\s*\)\s*\{",
     "请求执行 fork 炸弹"),
    (r"(?i)(curl|wget)[^\n]*(\|\s*(sh|bash)|\s+-O\s+-)",
     "请求下载脚本并执行"),
    (r"(?i)https?://[^\n|]+[^\n]*\|\s*(sh|bash)",
     "请求下载脚本并执行"),
    (r"(?i)\bbase64\b[^\n]*(\|\s*(sh|bash)|-d)",
     "请求使用编码载荷绕过检查"),
]


def screen_user_intent(query: str) -> RuleVerdict:
    text = query.strip()
    if not text:
        return RuleVerdict(decision=RuleDecision.DENY,
                           reason="空指令，拒绝执行", matched_rule="empty")
    candidates = (text, unicodedata.normalize("NFKC", text))
    for pattern, label in _BYPASS_PATTERNS + _DESTRUCTIVE_PATTERNS:
        if any(re.search(pattern, candidate) for candidate in candidates):
            return RuleVerdict(decision=RuleDecision.DENY,
                               reason=label, matched_rule=pattern)
    return RuleVerdict(decision=RuleDecision.REVIEW,
                       reason="未命中自然语言红线，进入后续安全流水线")

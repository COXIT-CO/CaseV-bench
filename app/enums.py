from enum import Enum


class DefaultModels(Enum):
    LLAMA_3 = "meta-llama/llama-3.3-70b-instruct:free"
    DEEPSEEK_R1 = "deepseek/deepseek-r1:free"
    NEMOTRON = "nvidia/nemotron-3-ultra-550b:free"
    QWEN_CODER = "qwen/qwen3-coder:free"
    GEMMA_4 = "google/gemma-4-26b-a4b-it:free"


class PromptStatus(Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    
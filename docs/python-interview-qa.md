# Python 面试 Q&A


## Q1: Python 的多线程、GIL 与锁

### 多线程

Python 有多线程（`threading` 模块），但 CPython 中有 GIL（Global Interpreter Lock），同一时刻只有一个线程能执行 Python 字节码。所以多线程对 CPU 密集型任务无法实现真正的并行，但对 IO 密集型任务（网络请求、文件读写）仍然有效——线程在等待 IO 时会释放 GIL，其他线程可以运行。

### GIL 的原因

CPython（Python 语言规范的默认实现，用 C 编写的执行引擎）的内存管理基于**引用计数**（`ob_refcnt`），每个对象都有一个引用计数器。多线程环境下，多个线程同时修改同一个对象的引用计数会产生**竞态条件**（Race Condition）——最终结果取决于线程执行顺序，导致引用计数偏低、对象被提前释放、程序崩溃。

如果给每个对象都加锁，开销太大且容易死锁。GIL 是一个粗粒度的折中方案——用一把全局锁保护所有对象的引用计数安全，实现简单，单线程性能也不受损。Python 3.13 开始实验性支持 `--disable-gil`（PEP 703），用更细粒度的锁和无锁引用计数替代 GIL。

### 多线程的锁

这些锁从 Python 早期版本就有，是 `threading` 模块的标准组件。它们和 GIL 保护的对象不同——GIL 保护 CPython 内部的引用计数安全，这些锁保护**用户代码中的共享数据**。即使有 GIL，用户代码仍然需要锁，因为 GIL 在字节码指令之间会释放，复合操作（读取-修改-写回）仍然不是原子的。

| 锁类型 | 作用 |
|--------|------|
| `Lock` | 互斥锁，同一时刻只有一个线程持有 |
| `RLock` | 可重入锁，同一线程可多次 acquire 不会死锁 |
| `Semaphore` | 信号量，允许最多 N 个线程同时访问 |
| `Condition` | 条件变量，线程间基于条件的等待/通知机制 |
| `Event` | 事件标志，一个线程设置信号，其他线程等待信号 |
| `Barrier` | 屏障，N 个线程都到达后才一起继续 |

### 结合本项目

TuningAgent 选择了 asyncio（单线程异步）而非多线程，原因是 Agent 的核心操作全是 IO 密集型（LLM API 调用、工具执行），asyncio 在单线程内通过协程切换实现并发，既避开了 GIL 的限制，也没有锁的复杂度。

项目中仍有少量线程和锁的使用：
- `asyncio.Event`：Agent 的取消机制，CLI 或 subagent 设置信号后 Agent loop 停止执行
- `threading.Event` + `threading.Thread`：CLI 中启动独立线程监听 ESC 键，通过 Event 信号控制监听器停止


## Q2: multiprocessing 和 threading 如何结合？（进程、线程、协程）

### 基本定义

- **进程**：操作系统分配资源的最小单位，拥有独立的内存空间
- **线程**：操作系统调度执行的最小单位，多个线程共享同一进程的内存
- **协程**：可以暂停和恢复执行的函数。执行到 `await` 时暂停并让出控制权，等条件满足后从暂停处恢复继续执行
- **阻塞**：当前执行单元等待某个操作完成期间无法做其他事情的状态。阻塞本身不占用 CPU，线程阻塞时 OS 会把 CPU 调度给其他线程，阻塞的线程只是占着内存"干等"

### 对比

| | 进程 (Process) | 线程 (Thread) | 协程 (Coroutine) |
|--|---------------|---------------|-----------------|
| 内存 | 独立地址空间 | 共享进程内存 | 共享线程内存 |
| 调度 | OS 调度 | OS 调度 | 用户态调度（事件循环） |
| 并行性 | 真正并行（多核） | 受 GIL 限制，CPU 任务无法并行 | 单线程内并发，无并行 |
| 开销 | 最重（创建、通信都贵） | 中等（MB 级/线程） | 最轻（KB 级/协程） |
| 适用场景 | CPU 密集型 | IO 密集型（阻塞式） | IO 密集型（非阻塞式） |

### 线程与协程的区别

两者都擅长 IO 密集型任务，但处理方式不同：

- **线程**：OS 调度，阻塞式 IO。线程 A 调 `requests.get()` 阻塞了，OS 自动切到线程 B。开发者不需要关心切换时机，但上千并发时内存开销大。
- **协程**：用户态调度，非阻塞 IO。协程 A 调 `await aiohttp.get()` 时主动让出控制权，事件循环切到协程 B。开销极小，轻松支持上万并发，但要求所有 IO 操作都用 async 库（即支持 `async/await` 的第三方库，如 `aiohttp`、`asyncpg`、`aiofiles`）。在协程中调同步库（如 `requests.get()`）不会让出控制权，事件循环会被阻塞。

### 如何结合

核心原则：**进程做计算，协程/线程做 IO**。

```
主进程（事件循环 / asyncio）
├── 协程 A: 发 LLM 请求（IO 等待，主动让出）
├── 协程 B: 发 LLM 请求（IO 等待，主动让出）
└── await run_in_executor(ProcessPoolExecutor)
    ├── 子进程 1: CPU 密集计算（真正并行）
    └── 子进程 2: CPU 密集计算（真正并行）
```

通过 `asyncio.run_in_executor()` 桥接——在协程中把 CPU 任务丢给进程池，不阻塞事件循环。

### 结合本项目

TuningAgent 主体用 asyncio 协程，因为核心操作全是 IO 密集型。但 CLI 中监听 ESC 键用了 `threading.Thread`，因为键盘监听是阻塞式的（等待用户按键的过程中调用不返回），没有 async 接口，如果放在事件循环中会卡住所有协程，所以用独立线程隔离。


## Q3: Python 的装饰器是什么？项目中用了哪些？

### 装饰器是什么

装饰器是一个接收函数并返回新函数的函数，用于在不修改原函数代码的前提下增加额外行为。

```python
@decorator
def func():
    pass

# 等价于
func = decorator(func)
```

### 项目中用到的装饰器

**1. `@property` / `@xxx.setter`（63 处，最多）**

对外暴露统一的属性访问接口。`@property` 让方法去掉括号直接当属性用（`obj.name` 而非 `obj.name()`），好处是内部实现从存储改成计算，调用方代码不用改。项目中 Tool 基类用 `@property` 定义 `name`、`description`、`parameters`，每个子类返回不同的值，Agent 侧统一用 `tool.name` 访问，不需要知道这些值是硬编码的还是动态生成的。

**2. `@classmethod` / `@staticmethod`（18 处）**

- `@classmethod`：接收类本身（`cls`）作为第一个参数，常用于工厂方法。项目中 `Config.load()` 就是类方法。
- `@staticmethod`：不接收 `self` 或 `cls`，和普通函数一样，只是逻辑上属于这个类。

**3. `@abstractmethod`（4 处）**

标记抽象方法，子类必须实现。`tuningagent/llm/base.py` 中定义 LLM 客户端接口，`AnthropicClient`、`OpenAIClient` 必须实现 `generate()`、`health_check()` 等方法。

**4. `@dataclass`（3 处）**

自动生成 `__init__`、`__repr__`、`__eq__` 等方法，减少样板代码。`@dataclass(slots=True)` 还能通过 `__slots__` 减少内存占用。

**5. `@async_retry`（自定义装饰器，项目核心）**

项目中最有代表性的自定义装饰器，在 `tuningagent/retry.py` 中实现。包装异步函数，自动添加指数退避重试逻辑：

```python
@async_retry(RetryConfig(max_retries=3, initial_delay=1.0))
async def call_api():
    pass
```

实现原理：`async_retry` 返回一个 `decorator`，`decorator` 用 `@functools.wraps(func)` 包装原函数，在 `wrapper` 中加入 for 循环 + 异常捕获 + 延时重试的逻辑。`functools.wraps` 保证包装后的函数保留原函数的名字和文档。

**6. `@kb.add("c-j")`（prompt_toolkit 快捷键绑定）**

CLI 中用装饰器注册快捷键处理函数，`@kb.add("c-j")` 把函数绑定到 Ctrl+J 按键事件。

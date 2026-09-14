# Python-Based Turmoil Simulation Framework for Testing AWS Cloud Interactions via LocalStack

## 1. Goal

The main goal of this thesis project is to systematically explore concurrent behaviors of multithreaded AWS client workloads by controlling the order in which their HTTP requests are allowed to proceed.

Instead of letting concurrent boto3 requests race naturally and accepting whatever order the operating system happens to produce, this project introduces a Flask-based simulator/proxy in front of LocalStack. Every request goes through the simulator first. The simulator identifies each request as a logical step, decides whether that step is allowed now, delayed, or dropped, forwards it to LocalStack when appropriate, and records the observed execution trace.

That trace is then used to generate more executions. In this way, the system does not perform only one run of the client; it performs many runs, each one exploring a different possible ordering or mutation of the same workload.

---

## 2. Big Picture of the Architecture

At a high level, the system has five main parts:

1. A client workload written with boto3 and multiple threads.
2. A simulator/proxy that receives every HTTP request before LocalStack.
3. A scheduler that decides which request may proceed next.
4. A prefix generator algorithm that creates new forced executions from previous traces.
5. A main runner that repeatedly launches the client until no unexplored prefixes remain.

The overall flow looks like this:

```text
Client threads
   |
   v
HTTP requests with X-Client-Id / X-Thread-Id
   |
   v
Simulator proxy (Flask)
   |
   +--> Scheduler decides:
   |       - allow now
   |       - delay
   |       - drop
   |
   v
LocalStack
   |
   v
HTTP response
   |
   v
Simulator records trace + result
   |
   v
Algorithm generates new forced prefixes
   |
   v
main.py starts next run
```

This architecture lets the thesis study interleavings in a controlled, repeatable way.

---

## 3. What a Step Means in This Thesis

In this project, the unit of scheduling is a step. A step is built from the request identity:

- client id
- thread id
- HTTP method
- request path

The helper builds it in the form:

```text
client:thread:method:path
```

So a request from one client thread is transformed into a single scheduling token. All later logic—free scheduling, forced prefixes, DROP, DELAY, trace recording, and prefix generation—works on these step tokens.

Also added pretty-printing so that an internal token can be shown in a human-readable form such as:

```text
client-conditional | Thread-2 | head_bucket(bucket-checked)
```

That makes the logs easier to read and discuss.

---

## 4. How the Client Participates in the Simulator

Our client workloads are written specifically to cooperate with the simulator. Each boto3 client registers a before-call hook and injects headers such as:

- X-Client-Id
- X-Thread-Id

These headers let the simulator know which logical client and which thread issued the request. Without these headers, the simulator could still proxy traffic, but it would lose the structure needed for meaningful concurrency exploration.

---

## 5. What main.py Does

main.py is the experiment orchestrator. It parses the experiment configuration, starts the simulator in-process, resets LocalStack, repeatedly launches the client workload, and reports the result of each run back to the simulator.

The repeated execution loop is central. One run produces one trace. That trace leads to more prefixes. Those prefixes cause more runs. The process continues until the simulator reports that exploration is complete.

The command-line usage currently follows this form:

```bash
python main.py [--404-as-fail] [--drop] [--delay] [--delay-for-N] [--408-as-timeout] <client_script_path>
```

---

## 6. Arguments and What They Mean in the Thesis

### 6.1 Normal execution

```bash
python main.py <client_script_path>
```

This is the baseline exploration mode. The system still performs multiple runs, still records traces, and still generates new prefixes, but it does so without DROP and without DELAY mutations. In this mode, the thesis studies normal interleavings of the workload.

### 6.2 --drop

```bash
python main.py --drop <client_script_path>
```

This enables DROP-based exploration. The algorithm allowed to create prefixes in which one step is replaced by a DROP token. During replay, when the scheduler matches that forced token, the simulator treats the step as dropped rather than forwarding it to LocalStack.

### 6.3 --delay

```bash
python main.py --delay <client_script_path>
```

This enables DELAY-based exploration. The algorithm allowed to create prefixes where one step is wrapped with a delay token. When that step is matched during execution, the simulator enforces a strict delay window before forwarding the request. Default delay is 20 seconds

### 6.4 --delay-for-N

```bash
python main.py --delay --delay-for-120 <client_script_path>
```

This is a compact way to define the delay duration. It overrides the default delay when DELAY mode is enabled.

### 6.5 --delay-seconds N

```bash
python main.py --delay --delay-seconds 20 <client_script_path>
```

This is the alternative syntax for the same idea.

### 6.6 --404-as-fail

```bash
python main.py --404-as-fail <client_script_path>
```

By default, our helper does not treat HTTP 404 as failure. This matters because operations such as head_bucket or other existence checks may naturally produce a 404 as part of normal control flow. With this flag enabled, those 404 responses are classified as failures instead.

### 6.7 --408-as-timeout

```bash
python main.py --408-as-timeout <client_script_path>
```

By default, our helper does not treat HTTP 408 as timeout. This matters because operations such as head_bucket or other existence checks may naturally produce a 408 as part of normal control flow in case delay occur. With this flag enabled, those 408 responses are classified as timeout instead.

---

## 7. Free Runs vs Forced Prefix Runs

A very important concept is the difference between a free run and a forced prefix run.

### Free run

In a free run, there is no prefix to enforce yet. The scheduler waits for candidate steps to arrive, gathers contenders for a short window, and then deterministically picks the lexicographically smallest step among those contenders.

### Forced prefix run

In a forced prefix run, the scheduler already has a sequence of expected steps. It no longer chooses freely. Instead, it waits for the next expected step to be presented. If the expected token is a wrapped token like DROP or DELAY, the scheduler still matches the underlying step, but records that the matched token had special semantics.

This distinction is the heart of the exploration strategy:

- free run = discover one execution trace
- forced run = replay a chosen prefix to explore another behavior

---

## 8. What an Interleaving Means Here

An interleaving is the order in which steps from different threads appear in the global execution trace.

Suppose one thread issues create_bucket called ```A``` and another issues head_bucket called ```B```. Depending on timing and scheduling, the global trace could be:

```text
A then B
```

or

```text
B then A
```

Those are different interleavings. Focus on how many meaningful interleavings exist, which ones are feasible, and which ones expose different behaviors.

Our algorithm currently uses a conservative adjacent cross-thread swap heuristic. When two adjacent steps in the trace come from different threads, it can generate a new forced prefix that tries to make the later one occur before the earlier one, while preserving same-thread prerequisites.

---

## 9. Prefixes: What They Are and Why They Matter

A prefix is an initial segment of an execution that the simulator enforces before allowing the remainder of the execution to proceed naturally. Prefixes are used to systematically explore different execution orders by controlling the sequence in which operations are executed.

Consider the following sequence of operations:

- A = create_bucket  
- B = head_bucket  
- C = delete_bucket  

```text
A B C
```

In this workflow, operation ```B``` depends on ```A```, and operation ```C``` depends on the result of ```B```. Specifically, ```C``` is executed only if ```B``` returns true (i.e., the bucket exists). If ```B``` returns false, then ```C``` is not executed.


Now consider a prefix such as:

```text
B A
```
In this case, the simulator forces ```B``` to execute before ```A```. Since the bucket has not yet been created, ```B``` will return false. As a result, the condition required to trigger ```C``` is not satisfied, and therefore ```C``` is never executed. Afterward, ```A``` executes normally, creating the bucket. Below 

<div style="display:flex; gap:40px;">

<div style="height:50%">

```
Client Thread-2        Simulator           LocalStack
      |                    |                   |
      | head_bucket (B)    |                   |
      |------------------->|                   |
      |                    | forward request   |
      |                    |------------------>|
      |                    | 404 (not exist)   |
      |                    |<------------------|
      | B = False          |                   |
      |<-------------------|                   |
      |                    |                   |
      | create_bucket (A)  |                   |
      |------------------->|                   |
      |                    | forward request   |
      |                    |------------------>|
      |                    | 200 OK            |
      |                    |<------------------|
      | A executed         |                   |
      |<-------------------|                   |
      |                    |                   |
      | C not executed     |                   |
```
</div></div>

The diagram illustrates how enforcing the prefix B → A changes the execution behavior. The head_bucket operation is executed first and returns false because the bucket does not yet exist. As a result, the conditional logic that would normally trigger delete_bucket is never satisfied. Only after this does the create_bucket operation execute, demonstrating how prefix-based scheduling can alter both execution order and program outcome.

---

# 10. BASIC EXECUTION

### EXECUTION WITHOUT SIMULATOR

In a standard execution environment without the simulator, the client communicates directly with the backend service. Requests are sent directly to LocalStack, and responses are returned immediately without any intermediate control or observation. While this approach allows the system to function normally, it provides no visibility into the exact ordering of operations and does not allow controlled exploration of different interleavings. As a result, only a single execution path is observed, and any concurrency-related behaviors depend entirely on timing and system conditions. This makes it difficult to reproduce specific interleavings or systematically analyze different execution scenarios.

<div style="display:flex; gap:40px;"><div style="height:50%">

```
Client Thread-1            LocalStack
      |                        |
      | create_bucket(...)     |
      |----------------------->|
      |                        |
      | 200 OK / success       |
      |<-----------------------|
      |                        |
```
</div></div>

This diagram shows the direct interaction between the client and LocalStack in the absence of the simulator. 

### EXECUTION WITH SIMULATOR

In the simple execution mode, the simulator operates without introducing artificial behaviors such as request dropping or delay injection. The process begins by starting the simulator, which launches a Flask-based HTTP proxy that intercepts all client requests directed toward the storage service. Before the first execution begins, LocalStack is reset to ensure a clean and consistent system state. This guarantees that each execution starts from an identical baseline, avoiding interference from previous runs. Once the environment is prepared, the selected client workload is executed. 

During execution, the simulator observes each request passing through the proxy and records the sequence of operations as an execution trace. Each request is treated as a distinct step and is scheduled according to the simulator’s internal scheduling policy. After the run completes, the algorithm analyzes the recorded trace and generates alternative prefixes that represent different possible execution orders. These prefixes are then used to guide subsequent executions, enabling systematic exploration of different interleavings. The system continues this process iteratively: executing the workload, recording traces, generating new prefixes, and replaying them. This loop continues until no new prefixes remain, indicating that all relevant execution paths have been explored. In this mode, the simulator focuses purely on exploring natural interleavings produced by scheduling decisions, without introducing additional mutations such as dropped or delayed requests.

<div style="display:flex; gap:40px;"><div style="height:50%">

```
Client Thread-1            Simulator                 LocalStack
      |                        |                         |
      | create_bucket(...)     |                         |
      |----------------------->|                         |
      |                        | forward request         |
      |                        |------------------------>|
      |                        |                         |
      |                        | 200 OK / success        |
      |                        |<------------------------|
      | return response        |                         |
      |<-----------------------|                         |
      |                        |                         |
```
</div></div>

The diagram above illustrates the normal execution flow in simple mode. The client sends a request to the simulator, which forwards it to LocalStack without modification. The backend processes the request and returns a response, which is then relayed back to the client. In addition to forwarding requests, the simulator records the execution trace, which is later used for systematic exploration.

## 11. DROP

A DROP represents a simulated drop in which a client request is removed from the execution path before reaching the backend service. In this scenario, the operation still appears in the execution trace recorded by the simulator, but it is not actually executed by the localstack. This mechanism models situations in distributed systems where requests may be lost, skipped, or never processed due to network faults, message loss, or concurrency-related issues. The DROP argument enables the simulator to explore such behaviours by preventing selected client operations from being forwarded to the LocalStack backend. During exploration, the algorithm generates execution prefixes in which specific operations are marked as dropped. When the simulator encounters such a step during execution, it intercepts the request at the proxy layer, records the operation in the trace, and deliberately prevents it from reaching the backend service. This is valuable because many concurrent systems behave differently when an operation is absent rather than merely late. With DROP, the simulator does not forward the request to LocalStack. That means the backend state does not change in the way the client expected.

Instead of forwarding the request to LocalStack, the simulator immediately generates a synthetic artifact response and returns it to the client. As a result, the client may observe a successful operation even though the backend system never executed it. This allows the simulator to study how client logic behaves when operations unexpectedly disappear from the execution sequence. The drop therefore occurs entirely on the simulator side, before the request reaches the backend service, rather than during the response phase between LocalStack and the simulator. For example, in a single-threaded client workload that attempts to create a bucket, the client sends a request to the simulator as usual. Under normal execution, the simulator forwards the request to LocalStack and returns the real response. However, when DROP exploration is enabled, the simulator may intercept the bucket creation request and treat it as dropped. The client still receives a response from the simulator, but the bucket is never created in the backend system.

<div style="display:flex; gap:40px;">

<div style="height:50%">

```
Client Thread-1            Simulator                 LocalStack
      |                        |                         |
      | create_bucket(...)     |                         |
      |----------------------->|                         |
      |                        | decide: DROP step       |
      |                        |------------X-------     |
      |                        | not forwarded           |
      |                        |                         |
      | artifact response      |                         |
      |<-----------------------|                         |
      |                        |                         |
```
</div>
</div>

The diagram illustrates how request handling differs between normal execution and DROP exploration. In normal execution, the client sends a request to the simulator, which forwards the request to LocalStack. LocalStack processes the operation and returns a response to the simulator, which then forwards the response back to the client. In contrast, when DROP exploration is enabled, the simulator intercepts the client request and prevents it from being forwarded to LocalStack. Instead, the simulator immediately returns a synthetic response to the client. As a result, the client receives a response even though the backend service never executed the operation. This behavior demonstrates that the DROP mutation occurs entirely at the simulator layer before the request reaches the backend system.

---

## 12. DELAY

The `--delay` argument introduces controlled execution delays for selected operations within the simulator. Unlike the DROP mechanism, where a request is never forwarded, delayed operations are eventually executed by the backend service. The simulator temporarily postpones forwarding the request, thereby simulating real-world conditions such as network latency, service congestion, or scheduling delays in distributed systems. This allows the system to study how timing variations influence the behavior of concurrent client workloads.

When delay exploration is enabled, the simulator intercepts a scheduled operation and pauses execution for a predefined duration before forwarding it to the backend. During this pause, the simulator may block or control the progression of other steps to preserve the intended scheduling effect. Once the delay period expires, the request is forwarded to LocalStack and processed normally. The client ultimately receives a valid backend response, but with an artificially extended response time. For example, in a single-thread bucket creation workload, the `create_bucket` request is delayed before being sent to the backend, yet it still completes successfully after the delay interval.

<div style="display:flex; gap:40px;">

<div style="height:50%">

```
Client Thread-1            Simulator                 LocalStack
      |                        |                         |
      | create_bucket(...)     |                         |
      |----------------------->|                         |
      |                        | delay step              |
      |                        |   (sleep...)            |
      |                        |                         |
      |                        | forward request         |
      |                        |------------------------>|
      |                        |                         |
      |                        | 200 OK / success        |
      |                        |<------------------------|
      | return response        |                         |
      |<-----------------------|                         |
      |                        |                         |

```
</div>
</div>

The diagram illustrates the behavior of a delayed operation. The client sends a request to the simulator, which identifies the step as subject to delay. Instead of forwarding the request immediately, the simulator pauses execution for a specified duration. After this delay, the request is forwarded to LocalStack, where it is processed normally. The backend returns a standard response, which is then relayed back to the client. Although the functional outcome remains unchanged, the timing of the response is intentionally altered, allowing the system to explore timing-dependent behaviors in concurrent executions.

---

## 13. Why DROP and DELAY Are Different

These two thesis mechanisms model different behaviors:

- DROP means the backend never sees the request.
- DELAY means the backend sees the request later.

That difference matters a lot for concurrent AWS-style clients. A delayed create may still let a later head_bucket race before it. A dropped create means the bucket never appears at all.

---

## 14. How the Scheduler Actually Enforces Order

The scheduler has two jobs:

1. determine what should happen next
2. detect when a forced prefix is stuck

In free mode, it gathers candidate steps and chooses deterministically. In enforcing mode, it waits for the exact expected logical step. If the expected item is DROP step and the client presents the matching normal step, the scheduler counts that as a match and records that this was a DROP match.

If the expected item is DELAY step and the client presents the matching normal step, the scheduler counts that as a match and records that this was a DELAY match with the configured seconds.

---

## 15. Timeout and the Watchdog

Forced execution can become stuck. For example, the scheduler may be waiting for a step that never appears because the branch leading to that step was not taken. To prevent the simulator from waiting forever, the scheduler tracks the time since the last progress. If the wait exceeds the configured deadlock timeout, it marks the run as TIMEOUT.

This is essential for the thesis because some prefixes are simply not feasible under the program logic.


---

## 16. Trace Recording and Deduplication

At the end of each run, the simulator records the completed trace and classifies the run. It also performs trace deduplication so that repeated traces do not keep generating the same prefixes again. This prevents wasted exploration and makes the thesis experiments cleaner.

---

## 17. Execution Result Classification

The outcome of each execution is determined based on both the **client process exit status** and the **simulator-side classification**. The system combines these signals to categorize each run into one of the following result types.

### SUCCESS

A run is classified as **SUCCESS** when the client process completes normally with **exit code 0**, and no failure or timeout condition is reported by the simulator.

This indicates that:
- All client threads executed successfully
- No unhandled exceptions occurred
- No timeout or failure condition was triggered during execution

In other words, both the client and simulator agree that the execution completed correctly.

### FAILURE

A run is classified as **FAILURE** when the client process completes execution but reports an error condition.

This typically occurs when:
- The client exits with a **non-zero exit code** (excluding crash-specific codes)
- The client explicitly flags an error (e.g., `CLIENT_HAD_ERROR`)
- The simulator classifies an HTTP response (such as 404) as a failure, depending on configuration

In this case, the execution completed, but the observed behavior is considered incorrect according to the client logic or simulator rules.

### CRASH

A run is classified as **CRASH** when a client thread encounters an **unhandled exception** and terminates abnormally.

This is typically detected by:
- A thread-level exception that is not caught
- A custom thread `excepthook` or error handler marking the execution as crashed
- The client exiting with a specific crash-related exit code (e.g., a dedicated non-zero code)

A crash indicates that the program did not complete its intended logic due to an unexpected failure.

### TIMEOUT

A run is classified as **TIMEOUT** when execution fails to complete within expected conditions or progress cannot be made.

This can occur in several situations:

- The **forced-prefix watchdog** waits too long for the next expected step and no matching request arrives
- The client reports a **timeout-related error** (e.g., request timeout or connection abort)
- The simulator is unable to forward or coordinate requests in a timely manner (e.g., due to delay injection or blocked scheduling)

In such cases, the execution is considered incomplete because the system could not make further progress within the allowed time.

---

## 18. Why Resetting LocalStack Matters

Before the first execution, and again between runs inside the simulator workflow, the system resets LocalStack state. This is necessary because you want each explored execution to begin from a clean baseline, not from the leftover effects of a previous run.

Without reset, a later run could appear to be a new interleaving when it was really just influenced by state pollution from an earlier experiment.

---

## 19. A Compact Visual Summary

### 19.1 Overall experiment loop

```text
Run client
   |
   v
Observe trace
   |
   v
Generate prefixes
   |
   v
Replay next prefix
   |
   v
Observe new result
   |
   v
Repeat until no prefixes remain
```

### 19.2 Free run vs forced run

```text
FREE RUN:
  gather contenders -> choose deterministic next step -> continue

FORCED RUN:
  wait for expected step in prefix -> apply DROP/DELAY semantics if wrapped -> continue
```

### 19.3 Algorithm generation

```text
Observed trace
   |
   +--> adjacent cross-thread swap prefixes
   +--> single DROP prefixes
   +--> single DELAY prefixes
   +--> mixed DROP+DELAY prefixes on different events
```

### 19.4 Important constraint

```text
Allowed:
  DROP::A
  DELAY::20::A
  DROP::A + DELAY::B

Not allowed:
  DROP::DELAY::A
  DELAY::DROP::A
```

---

## 20. Practical Commands

Baseline:

```bash
python main.py <client_script_path>
```

DROP:

```bash
python main.py --drop <client_script_path>
```

DELAY:

```bash
python main.py --delay <client_script_path>
```

DELAY with custom duration:

```bash
python main.py --delay --delay-for-10 <client_script_path>
```

Strict 404 classification:

```bash
python main.py --404-as-fail <client_script_path>
```

Custom forced-prefix timeout:

```bash
python main.py --408-as-timeout <client_script_path>
```

---

## 21. Final Thesis Takeaway

The core idea of this thesis is not merely to run a concurrent client and observe what happens once. The core idea is to transform a concurrent client workload into a structured exploration problem. The simulator captures steps, the scheduler controls execution, DPOR generates new prefixes, and DROP or DELAY mutations expand the explored behavior space. Together, these mechanisms let the thesis reason about concurrency in a disciplined way: which interleavings exist, which are feasible, which outcomes they produce, and how sensitive the client logic is to missing or delayed operations.

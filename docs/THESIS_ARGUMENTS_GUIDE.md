# Simulator -- Command Line Arguments Guide

This document explains all the command-line arguments used in my simulator and what each argument does.

------------------------------------------------------------------------

## Basic Run (No Arguments)

``` bash
python main.py <client_file>.py
```

### What it does:

-   Starts the simulator (Flask proxy)
-   Resets LocalStack before execution
-   Explores normal interleavings only
-   Prints FINAL SUMMARY at the end

------------------------------------------------------------------------

## --drop

``` bash
python main.py --drop <client_file>.py
```

### What it does:

-   Enables DPOR DROP exploration
-   Simulator may intentionally skip (drop) certain steps
-   Used to explore alternative schedules
-   Produces additional executions
-   Dropped runs are counted separately in FINAL SUMMARY

Important: DROP is controlled exploration. It is NOT a timeout.

------------------------------------------------------------------------

## --delay 

``` bash
python main.py --delay <client_file>.py
```

### What it does:

-   Adds artificial delay inside the simulator before forwarding a
    request
-   By default: 20 seconds
-   Simulator sleeps for given seconds
-   Used to simulate slow network / timing window

------------------------------------------------------------------------

## --delay-for-second

``` bash
python main.py --delay --delay-for-<seconds> <client_file>.py
```

### What it does:

-   Adds artificial delay inside the simulator before forwarding a
    request
-   Let user decide how long delay gonna happen
-   Simulator sleeps for given seconds
-   Used to simulate slow network / timing window

If delay is large: - Client may give up waiting - Run becomes TIMEOUT

------------------------------------------------------------------------

## 404-as-Fail

``` bash
python main.py --404-as-Fail <client_file>.py
```

### What it means:

-   Controls whether HTTP 404 is considered a FAILURE
-   Depends on environment configuration
-   Affects final classification

------------------------------------------------------------------------

## 408-as-Timeout

``` bash
python main.py --408-as-Timeout <client_file>.py
```

### What it means:

-   Controls whether HTTP 408 is considered a Timeout
-   Depends on environment configuration
-   Affects final classification

------------------------------------------------------------------------

# FINAL SUMMARY Categories

At the end of execution:

-   Success → Client finished without error
-   Failure → Client reported error
-   Crash → Thread crashed via exception
-   Dropped → Step intentionally dropped (--drop)
-   Timeout → Client gave up waiting (--delay effect)
-   Infeasible → Forced prefix could not be executed

------------------------------------------------------------------------
def build_cmd(entrypoint: str, base_args: Dict[str, Any], overrides: Dict[str, Any], output_path: str) -> List[str]:
    engine_overrides, non_engine_overrides = _normalize_overrides_for_engine(overrides)
    # NOTE: non_engine_overrides는 evaluate/gate에서 활용하도록 호출부에서 사용 가능
    args = dict(base_args)
    args.update(engine_overrides)
    args["output"] = output_path

    # Auto-drop unsupported engine args to prevent crashes
    supported = _engine_supported_keys(entrypoint)
    if supported:
        dropped = {}
        for k in list(args.keys()):
            # allow internal keys that might not be in -h (rare), but output should be supported
            if k == "output":
                continue
            if str(k).startswith("--"):
                kk = str(k)[2:]
            else:
                kk = str(k)
            if kk not in supported:
                dropped[kk] = args[k]
                del args[k]
        if dropped:
            print(f"[WARN] auto-dropped unsupported args for engine={entrypoint}: {dropped}")

    cmd = ["python3", entrypoint]
    for k in sorted(args.keys()):
        opt = k if str(k).startswith("--") else f"--{k}"
        cmd.extend([opt, str(args[k])])
    return cmd

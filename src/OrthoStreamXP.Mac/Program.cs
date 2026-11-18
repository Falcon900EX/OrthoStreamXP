using System.Globalization;
using System.Runtime.InteropServices;
using OrthoStreamXP.Mac;

namespace OrthoStreamXP;

internal static class MacProgram
{
    public static async Task<int> Main(string[] args)
    {
        CultureInfo.DefaultThreadCurrentCulture = CultureInfo.InvariantCulture;
        CultureInfo.DefaultThreadCurrentUICulture = CultureInfo.InvariantCulture;
        try
        {
            if (args.Length == 0 || args[0] is "help" or "--help" or "-h") { Help(); return 0; }
            string command = args[0];
            var options = Options(args[1..]);
            string python = Value(options, "python") ?? Environment.GetEnvironmentVariable("ORTHOSTREAMXP_PYTHON") ?? "python3";
            string bridge = Value(options, "bridge") ?? Path.Join(AppContext.BaseDirectory, "bridge");
            var runtime = new MacRuntime(python, bridge, options.ContainsKey("direct-io"));
            string settingsPath = MacConfiguration.Expand(Value(options, "settings") ?? MacConfiguration.DefaultSettings);
            if (command == "init")
            {
                string? source = Value(options, "source"), packages = Value(options, "packages");
                if ((source is null) == (packages is null)) throw new ArgumentException("Give exactly one of --source PACKAGE or --packages PARENT");
                MacConfiguration.Initialize(settingsPath, source ?? packages!, packages is not null,
                    Required(options, "xplane"), Value(options, "cache"));
                return 0;
            }
            if (!OperatingSystem.IsMacOS() || RuntimeInformation.ProcessArchitecture != Architecture.Arm64)
                throw new PlatformNotSupportedException("Run this port natively on an Apple Silicon Mac, not under Rosetta");
            if (command == "doctor")
            {
                Console.WriteLine($".NET {Environment.Version}; {RuntimeInformation.OSDescription}; {RuntimeInformation.ProcessArchitecture}");
                Console.WriteLine(runtime.Control("doctor"));
                Console.WriteLine("The binding and cached mount table are available. Driver approval still needs the live smoke test.");
                return 0;
            }
            if (command == "mounts") { Console.WriteLine(runtime.Control("mounts")); return 0; }
            if (command == "unmount")
            {
                string target = MacConfiguration.Expand(Required(options, "mountpoint"));
                Console.WriteLine(runtime.Control(options.ContainsKey("force") ? ["unmount", target, "--force"] : ["unmount", target]));
                return 0;
            }
            if (command is not ("run" or "check")) throw new ArgumentException($"Unknown command: {command}");
            Console.WriteLine(runtime.Control("doctor"));
            MountSettings settings = MacConfiguration.Load(settingsPath, runtime);
            foreach (PackagePlan plan in settings.Packages)
                Console.WriteLine($"{plan.Source}\n  -> {plan.Mountpoint}");
            if (command == "check") { Console.WriteLine("Configuration and mountpoint checks passed; nothing mounted."); return 0; }
            return await RunAsync(settings, runtime, Value(options, "ready-file")).ConfigureAwait(false);
        }
        catch (Exception ex) when (ex is ArgumentException or IOException or SettingsException or PlatformNotSupportedException or System.ComponentModel.Win32Exception)
        {
            Console.Error.WriteLine($"ERROR: {ex.Message}");
            return 1;
        }
        finally { Log.Close(); }
    }

    private static async Task<int> RunAsync(MountSettings settings, MacRuntime runtime, string? readyFile)
    {
        if (OperatingSystem.IsWindows()) throw new PlatformNotSupportedException();
        Log.Configure(settings.LogOutput, settings.LogPath, settings.LogDetail);
        TaskCompletionSource shutdown = NewSignal();
        ConsoleCancelEventHandler cancel = (_, e) => { e.Cancel = true; shutdown.TrySetResult(); };
        Console.CancelKeyPress += cancel;
        using var terminate = PosixSignalRegistration.Create(PosixSignal.SIGTERM, c => { c.Cancel = true; shutdown.TrySetResult(); });
        using var hangup = PosixSignalRegistration.Create(PosixSignal.SIGHUP, c => { c.Cancel = true; shutdown.TrySetResult(); });
        var hosts = new MacMountHostFactory(runtime);
        MountSession session = MountSession.Create(settings, hosts);
        try
        {
            bool failed = false;
            try
            {
                session.StartAll();
                if (readyFile is not null) File.WriteAllText(MacConfiguration.Expand(readyFile), "ready\n");
                Console.WriteLine("Mounts are ready. Start X-Plane now. Quit X-Plane before Ctrl+C here.");
                Console.WriteLine($"Log: {settings.LogPath}");
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or InvalidOperationException or System.ComponentModel.Win32Exception)
            {
                Log.Error("Mount startup failed; detaching any mounts already created", ex);
                failed = true;
                shutdown.TrySetResult();
            }
            while (true)
            {
                await Task.WhenAny(shutdown.Task, Task.Delay(TimeSpan.FromSeconds(2))).ConfigureAwait(false);
                if (!shutdown.Task.IsCompleted && hosts.Failure is null) continue;
                if (hosts.Failure is { } name)
                {
                    Log.Error($"macFUSE worker exited unexpectedly: {name}. Close X-Plane; stopping the remaining mounts.");
                    failed = true;
                }
                try
                {
                    await session.StopAsync().ConfigureAwait(false);
                    break;
                }
                catch (IOException ex)
                {
                    Log.Warning(ex.Message);
                    Console.Error.WriteLine("Serving processes have been retained. Close X-Plane, then press Ctrl+C to retry.");
                    shutdown = NewSignal();
                    await shutdown.Task.ConfigureAwait(false);
                }
            }
            session.Dispose();
            return failed ? 1 : 0;
        }
        finally
        {
            Console.CancelKeyPress -= cancel;
            // Do not unconditionally dispose here: killing the engine under a busy FUSE mount is unsafe.
        }
    }

    private static TaskCompletionSource NewSignal() => new(TaskCreationOptions.RunContinuationsAsynchronously);

    private static Dictionary<string, string?> Options(string[] args)
    {
        string[] flags = ["direct-io", "force"];
        string[] valued = ["source", "packages", "xplane", "cache", "settings", "python", "bridge", "mountpoint", "ready-file"];
        var result = new Dictionary<string, string?>(StringComparer.Ordinal);
        for (int i = 0; i < args.Length; i++)
        {
            string arg = args[i];
            if (!arg.StartsWith("--", StringComparison.Ordinal)) throw new ArgumentException($"Expected an option, got {arg}");
            string key = arg[2..];
            if (result.ContainsKey(key)) throw new ArgumentException($"Duplicate option --{key}");
            if (flags.Contains(key, StringComparer.Ordinal)) { result.Add(key, null); continue; }
            if (!valued.Contains(key, StringComparer.Ordinal) || ++i == args.Length || args[i].StartsWith("--", StringComparison.Ordinal))
                throw new ArgumentException($"Unknown option or missing value: --{key}");
            result.Add(key, args[i]);
        }
        return result;
    }

    private static string? Value(Dictionary<string, string?> options, string key) => options.GetValueOrDefault(key);
    private static string Required(Dictionary<string, string?> options, string key) => Value(options, key)
        ?? throw new ArgumentException($"Required: --{key}");

    private static void Help() => Console.WriteLine("""
        OrthoStreamXP macOS / Apple Silicon development port (GPL-3.0)
        Commands:
          doctor                         Check ARM64 Python, macFUSE library and mount table
          init --source PACKAGE --xplane XPLANE [--cache CACHE] [--settings FILE]
          init --packages PARENT --xplane XPLANE [--cache CACHE] [--settings FILE]
          check [--settings FILE]        Validate without mounting or modifying scenery
          run [--settings FILE]          Mount; Ctrl+C stops after X-Plane closes
          mounts                         List this port's mounts
          unmount --mountpoint PATH [--force]   Explicit recovery; never touches AutoOrtho mounts
        Common options: --python EXECUTABLE, --bridge DIRECTORY
        run option: --direct-io (diagnostic only; may disable mmap, not the default)
        No WPF GUI, macFUSE installer, automatic X-Plane launch or macOS auto-updater.
        """);
}

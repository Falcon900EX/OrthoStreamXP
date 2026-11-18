using System.Diagnostics;

namespace OrthoStreamXP.Mac;

internal sealed class MacRuntime(string python, string bridge, bool directIo = false)
{
    public string Python { get; } = python;
    public string Bridge { get; } = Path.GetFullPath(bridge);
    public bool DirectIo { get; } = directIo;

    public ProcessStartInfo PythonProcess(string script, params string[] arguments)
    {
        string path = Path.Join(Bridge, script);
        if (!File.Exists(path)) throw new FileNotFoundException("macOS bridge not found; build/publish the Mac project first", path);
        var start = new ProcessStartInfo(Python)
        {
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            RedirectStandardInput = true,
        };
        start.ArgumentList.Add("-u");
        start.ArgumentList.Add(path);
        foreach (string argument in arguments) start.ArgumentList.Add(argument);
        start.Environment["PYTHONDONTWRITEBYTECODE"] = "1";
        return start;
    }

    public string Control(params string[] arguments)
    {
        using var process = new Process { StartInfo = PythonProcess("bridge_control.py", arguments) };
        process.Start();
        process.StandardInput.Close();
        Task<string> output = process.StandardOutput.ReadToEndAsync();
        Task<string> error = process.StandardError.ReadToEndAsync();
        if (!process.WaitForExit(15000))
        {
            try { process.Kill(); }
            catch (InvalidOperationException) { /* Already exited. */ }
            throw new IOException("macOS control helper timed out; no mount directories were removed");
        }
        string text = output.GetAwaiter().GetResult();
        string details = error.GetAwaiter().GetResult();
        if (process.ExitCode != 0) throw new IOException(details.Trim().Length > 0 ? details.Trim() : "macOS control helper failed");
        return text.Trim();
    }
}

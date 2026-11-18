using System.Text.RegularExpressions;
using OrthoStreamXP.Setup;

namespace OrthoStreamXP.Mac;

internal static partial class MacConfiguration
{
    public static string DefaultSettings => Path.Join(AppPaths.Root, "orthostreamxp-mac.ini");

    public static MountSettings Load(string file, MacRuntime runtime)
    {
        SettingValues values = SettingsOptions.Schema.ReadFile(file);
        string[] pathNames = ["source", "base-packages", "cache", "xplane", "mountpoint", "lazy-texture-store"];
        // INI paths are literal, just as upstream: init expands ~; handwritten files need absolute paths.
        string[] paths = [.. pathNames.Select(values.String).OfType<string>()];
        foreach (string path in paths)
            if (!Path.IsPathFullyQualified(path)) throw new SettingsException($"Use an absolute path in {file}: {path}");
        if (paths.Length > 0) runtime.Control(["guard", .. paths]);
        MountSettings settings = MountSettings.FromValues(values, validateMountpoints: false,
            migrateLegacy: false, allowSourceInCustomScenery: true);
        // Never reuse AutoOrtho's z_ao_* mountpoints. Preserve its source package and config.
        PackagePlan[] plans = [.. settings.Packages.Select(p =>
        {
            string name = settings.CustomScenery is null ? Path.GetFileName(p.Mountpoint) : "z_OrthoStreamXP_" + SafeName(p.Name);
            string mount = settings.CustomScenery is null ? p.Mountpoint : Path.Join(settings.CustomScenery, name);
            runtime.Control("check", "--source", p.Source, "--cache", settings.Cache, "--mountpoint", mount);
            return p with { Name = name, Mountpoint = mount };
        })];
        if (plans.Select(p => p.Mountpoint).Distinct(StringComparer.OrdinalIgnoreCase).Count() != plans.Length)
            throw new SettingsException("Source package names map to duplicate macOS mount names");
        return settings with { Packages = plans };
    }

    public static void Initialize(string file, string source, bool multiple, string xplane, string? cache)
    {
        file = Expand(file);
        if (File.Exists(file)) throw new IOException($"Settings already exist; not overwriting {file}");
        source = Expand(source);
        xplane = Expand(xplane);
        cache = Expand(cache ?? Path.Join(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Library", "Caches", "OrthoStreamXP"));
        Directory.CreateDirectory(Path.GetDirectoryName(file)!);
        string text = $"""
            # OrthoStreamXP macOS/Apple Silicon. Source package stays read-only.
            # Absolute paths; spaces need no quotes. Stop AutoOrtho before mounting.
            {(multiple ? "base-packages" : "source")} = {source}
            xplane = {xplane}
            cache = {cache}
            # Initially manage scenery_packs.ini manually; do not duplicate AutoOrtho's mesh entries.
            scenery-packs = off
            texture-provider = bing,esri
            level-change = -1
            mip-streaming = on
            track-aircraft = true
            prefetch-loaded-scenery = on
            lazy-terrain-listing = true
            near-wait = 60
            max-wait = 6
            download-workers = 8
            encoder-workers = 4
            build-memory = 512
            cache-limit = 25
            match-colours = on
            seasons = off
            night-lights = off
            log-output = both
            read-log = off
            """;
        using var stream = new FileStream(file, FileMode.CreateNew, FileAccess.Write, FileShare.None);
        using var writer = new StreamWriter(stream);
        writer.WriteLine(text);
        Console.WriteLine($"Created {file}\nReview the settings, then run: bash scripts/run-macos.sh run --settings \"{file}\"");
    }

    public static string Expand(string path) => Path.GetFullPath(path == "~"
        ? Environment.GetFolderPath(Environment.SpecialFolder.UserProfile)
        : path.StartsWith("~/", StringComparison.Ordinal)
            ? Path.Join(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), path[2..]) : path);

    private static string SafeName(string name) => NamePattern().Replace(name, "_");
    [GeneratedRegex("[^A-Za-z0-9_.-]")]
    private static partial Regex NamePattern();
}

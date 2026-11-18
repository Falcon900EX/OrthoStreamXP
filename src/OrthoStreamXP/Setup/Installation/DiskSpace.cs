namespace OrthoStreamXP.Setup;

internal static class DiskSpace
{
    public static long? FreeBytes(string? folder)
    {
        try
        {
            return string.IsNullOrWhiteSpace(folder) || Path.GetPathRoot(Path.GetFullPath(folder)) is not { Length: > 0 } root
                ? null
                : new DriveInfo(root).AvailableFreeSpace;
        }
        catch (Exception ex) when (ex is IOException or ArgumentException or UnauthorizedAccessException)
        {
            return null;
        }
    }

}

# UI Automation probe — enumerate pet window's UIA tree to find clickable elements.
# This bypasses mouse_event entirely; UIA invokes elements by accessibility name.
Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes

$petHwnd = [IntPtr]38210094
$root = [System.Windows.Automation.AutomationElement]::RootElement

# Find the pet window by hwnd (NativeWindowHandle property)
$cond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::NativeWindowHandleProperty,
    [int]$petHwnd
)
$pet = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
if (-not $pet) {
    Write-Output "PET_UIA_NOT_FOUND hwnd=$petHwnd"
    exit 1
}

Write-Output "PET_FOUND name='$($pet.Current.Name)' class='$($pet.Current.ClassName)'"

# Walk the full descendant tree and list every Button/Hyperlink with Name
$walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
function Walk-Tree($el, $depth) {
    if ($depth -gt 6) { return }
    $info = $el.Current
    $ctrlType = $info.ControlType.LocalizedControlType
    if ($info.Name -or $ctrlType -eq 'button' -or $ctrlType -eq 'edit') {
        $pad = '  ' * $depth
        Write-Output "$pad[$ctrlType] name='$($info.Name)' id='$($info.AutomationId)' visible=$(-not $info.IsOffscreen)"
    }
    $child = $walker.GetFirstChild($el)
    while ($child) {
        Walk-Tree $child ($depth + 1)
        $child = $walker.GetNextSibling($child)
    }
}
Walk-Tree $pet 0

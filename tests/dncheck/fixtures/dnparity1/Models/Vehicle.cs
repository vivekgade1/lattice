namespace DnParity1.Models;

public class Vehicle
{
    public int Id { get; set; }

    public string Plate { get; set; } = "";
}

public class Car : Vehicle
{
    public int Doors { get; set; }
}

public class Truck : Vehicle
{
    public int PayloadKg { get; set; }
}

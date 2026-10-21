using DnParity1.Models;
using DnParity1.Services;
using Microsoft.EntityFrameworkCore;

namespace DnParity1.Data;

public class AppDb : DbContext
{
    private readonly ICurrentUser _current;

    public AppDb(DbContextOptions<AppDb> options, ICurrentUser current) : base(options)
    {
        _current = current;
    }

    public DbSet<User> Users { get; set; } = null!;

    public DbSet<Note> Notes { get; set; } = null!;

    public DbSet<Tag> Tags { get; set; } = null!;

    public DbSet<Todo> Todos { get; set; } = null!;

    public DbSet<Vehicle> Vehicles { get; set; } = null!;

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        modelBuilder.ApplyConfigurationsFromAssembly(typeof(AppDb).Assembly);
        modelBuilder.Entity<User>().OwnsOne(u => u.Address);
        modelBuilder.Entity<User>().Property(u => u.Role).HasDefaultValue("Member");
        modelBuilder.Entity<Vehicle>().HasDiscriminator<string>("Kind").HasValue<Car>("car").HasValue<Truck>("truck");
        modelBuilder.Entity<Todo>().HasQueryFilter(t => t.OwnerId == _current.UserId);
    }
}
